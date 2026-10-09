"""REM (Recalibrate Everything in Memory) cycle engine.

Coordinates periodic knowledge consolidation operations. ``DEFAULT_SCHEDULES``
in ``rem_config`` is the register of which operations exist and how often each
one runs; a list here would be a second register, and the second one is the one
that rots — this docstring named four of the seven for several releases.
"""

import json
import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .persistence import LessonStore
from .rem_config import DEFAULT_SCHEDULES, OperationSchedule, is_due, next_due_session

logger = logging.getLogger("mgcp.rem")


@dataclass
class RemFinding:
    """A single finding from a REM cycle operation."""

    operation: str  # Which operation produced this
    title: str  # Short description
    description: str  # Full explanation
    options: list[dict[str, str]] = field(default_factory=list)  # label + description
    recommended: int = 0  # Index of recommended option
    metadata: dict = field(default_factory=dict)  # Extra data for action execution


@dataclass
class RemReport:
    """Complete report from a REM cycle run."""

    session_number: int
    timestamp: str
    operations_run: list[str]
    operations_skipped: list[str]
    findings: list[RemFinding]
    duration_ms: float = 0.0


# How many queries must have run SINCE a lesson was created before "search has
# never matched it" means the trigger is wrong rather than that nothing has
# asked yet.
#
# Measured on a live store: 1,192 queries over 212 sessions, a mean of 5.6 per
# session and a median of 3. So 20 opportunities is roughly four to six sessions
# of real use. The test this replaces was "created more than 30 days ago", which
# hid 30 of 44 never-matched lessons behind a calendar that says nothing about
# whether anything tried to find them. A lesson nothing can retrieve is broken
# on the day it is written, not thirty days later.
MIN_QUERY_OPPORTUNITIES = 20

# Used only when telemetry cannot be read, so opportunities cannot be counted.
# Then age is the only signal left, and it is reported as the weaker one.
FALLBACK_AGE_DAYS = 14

# A proposed link has to clear the same bar the community bridge uses to append
# a lesson, because both answer "is this related enough to put in front of
# someone". One number, not two that drift apart.
LINK_MIN_SCORE = 0.55
LINK_CANDIDATE_POOL = 8
LINKS_PER_LESSON = 3
MAX_LINK_FINDINGS = 25


def _query_times() -> list[datetime] | None:
    """Every recorded query time, oldest first, or None when unreadable.

    Read straight from telemetry.db, the same way ``_gate_audit_review`` reads
    gate_audit.jsonl. None means "cannot measure", which the caller must not
    treat as "zero opportunities": that would flag every lesson on a machine
    with no telemetry.
    """
    import os
    import sqlite3
    from pathlib import Path

    path = Path(
        os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    ) / "telemetry.db"
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT timestamp FROM events WHERE event_type = 'query' "
                "ORDER BY timestamp"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(f"Could not read query history for the staleness scan: {exc}")
        return None

    out = []
    for (stamp,) in rows:
        try:
            out.append(datetime.fromisoformat(stamp))
        except (TypeError, ValueError):
            continue
    return out


def _duplicate_finding(pair: dict) -> RemFinding:
    """One trigger collision, named with the words both lessons answer to."""
    first = pair.get("lesson_1") or {}
    second = pair.get("lesson_2") or {}
    id_a, id_b = first.get("id", "?"), second.get("id", "?")
    overlap = pair.get("trigger_overlap") or 0.0
    shared = ", ".join(pair.get("shared_words") or [])
    return RemFinding(
        operation="duplicate_detection",
        title=f"Same retrieval ({overlap:.0%}): {id_a} / {id_b}",
        description=(
            f"'{id_a}' and '{id_b}' share {overlap:.0%} of their trigger words, "
            f"so a query matching one tends to match the other. Shared: "
            f"{shared}.\n\n"
            f"A: {first.get('trigger', '')[:120]}\n"
            f"B: {second.get('trigger', '')[:120]}"
        ),
        options=[
            {"label": "Merge into one",
             "description": ("refine the lesson you keep so its trigger absorbs "
                             "the other's words, then delete the other")},
            {"label": "Narrow one trigger",
             "description": "they are different rules that answer to the same words"},
            {"label": "Keep both", "description": "the overlap is harmless here"},
        ],
        # An identical trigger word set is a collision by construction, so
        # merging is the recommendation. Below that it is a judgement, and the
        # shared words are the evidence for making it.
        recommended=0 if overlap >= 0.9 else 1,
        metadata={"lesson_a": id_a, "lesson_b": id_b,
                  "trigger_overlap": overlap,
                  "shared_words": pair.get("shared_words") or [],
                  "similarity": pair.get("similarity")},
    )


def _link_finding(lesson, neighbours: list[tuple[str, float]]) -> RemFinding:
    """One proposal: this isolated lesson, and what it is nearest to."""
    listed = ", ".join(f"{other} ({score:.2f})" for other, score in neighbours)
    return RemFinding(
        operation="link_suggestions",
        title=f"No links at all: {lesson.id}",
        description=(
            f"'{lesson.id}' has no parent, no children and no typed "
            f"relationships, so the community bridge can never append it. "
            f"Nearest by meaning: {listed}. "
            f"Trigger: \"{lesson.trigger[:100]}\""
        ),
        options=[
            {"label": "Link to the nearest",
             "description": (f"link_lessons('{lesson.id}', "
                             f"'{neighbours[0][0]}', 'related')")},
            {"label": "Pick a different target",
             "description": "one of the others listed above fits better"},
            {"label": "Leave it isolated",
             "description": "it belongs to no cluster here"},
        ],
        recommended=0,
        metadata={
            "lesson_id": lesson.id,
            "proposed_links": [
                {"source_id": lesson.id, "target_id": other,
                 "relationship_type": "related", "score": score}
                for other, score in neighbours
            ],
        },
    )


def _overflow_finding(total: int) -> RemFinding:
    """One row standing for the unlinked lessons this run did not get to."""
    return RemFinding(
        operation="link_suggestions",
        title=f"{total - MAX_LINK_FINDINGS} more unlinked lessons",
        description=(
            f"{total} lessons carry no edges. The {MAX_LINK_FINDINGS} above are "
            f"this run's batch. The rest are on the record in the dashboard's "
            f"REM view, and the next cycle proposes links for them."
        ),
        options=[{"label": "Acknowledged",
                  "description": "work through the batch above first"}],
        recommended=0,
        metadata={"unlinked_total": total},
    )


def _queries_since(stamps: list[datetime] | None, moment: datetime) -> int | None:
    """How many queries ran after ``moment``, or None when it cannot be counted."""
    if stamps is None:
        return None
    return len(stamps) - bisect_right(stamps, moment)


class RemEngine:
    """Orchestrates REM cycle operations."""

    def __init__(
        self,
        store: LessonStore,
        schedules: dict[str, OperationSchedule] | None = None,
        *,
        project_id: str,
        vector_store=None,
        vector_store_factory=None,
    ):
        self.store = store
        # duplicate_detection needs the caller's live Qdrant client: local mode
        # allows one client per path, so an engine running inside the MCP server
        # cannot open its own. None is valid only out-of-process (the CLI).
        self.vector_store = vector_store
        # Prefer a factory. Only one of the seven operations needs a vector
        # store, and passing an open one made every cycle take the Qdrant lock
        # even when that operation was not due. Since the commit gate forces a
        # cycle before every commit, a session that never searched anything
        # still held the lock for the rest of its life. The factory is awaited
        # once, inside the operation that needs it.
        self._vector_store_factory = vector_store_factory
        self._factory_called = False
        self.schedules = schedules or DEFAULT_SCHEDULES
        # Which project's schedule cursor this engine reads and writes. The
        # corpus it maintains is global; only the cadence is per project.
        self.project_id = project_id

    async def get_due_operations(self, session_number: int) -> list[str]:
        """Determine which operations are due at this session number."""
        states = await self.store.get_rem_state(self.project_id)
        state_map = {s["operation"]: s["last_run_session"] for s in states}

        due = []
        for op_name, schedule in self.schedules.items():
            last_run = state_map.get(op_name, 0)
            if is_due(schedule, session_number, last_run):
                due.append(op_name)
        return due

    async def get_status(self, session_number: int) -> list[dict]:
        """Get schedule status for all operations at this session number.

        ``is_due`` is the same predicate ``get_due_operations`` uses, so what
        rem_status displays and what rem_run executes cannot disagree.
        """
        states = await self.store.get_rem_state(self.project_id)
        state_map = {s["operation"]: s for s in states}

        status = []
        for op_name, schedule in self.schedules.items():
            state = state_map.get(op_name)
            last_run = state["last_run_session"] if state else 0
            next_session = next_due_session(schedule, last_run)

            entry = {
                "operation": op_name,
                "strategy": schedule.strategy,
                "last_run_session": last_run,
                "next_due_session": next_session,
                "is_due": is_due(schedule, session_number, last_run),
            }

            status.append(entry)
        return status

    async def run(
        self,
        session_number: int,
        operations: list[str] | None = None,
    ) -> RemReport:
        """Run REM cycle operations.

        Args:
            session_number: Current session number for schedule tracking.
            operations: Specific operations to run. If None, runs all due operations.
        """
        start = datetime.now(UTC)

        if operations is None:
            operations = await self.get_due_operations(session_number)

        all_ops = list(self.schedules.keys())
        skipped = [op for op in all_ops if op not in operations]
        findings: list[RemFinding] = []

        for op in operations:
            try:
                op_findings = await self._run_operation(op, session_number)
                findings.extend(op_findings)
            except Exception as e:
                logger.error(f"REM operation {op} failed: {e}")
                findings.append(RemFinding(
                    operation=op,
                    title=f"{op} failed",
                    description=f"Error: {e}",
                    options=[{"label": "Acknowledged", "description": "Dismiss this error"}],
                ))

        elapsed = (datetime.now(UTC) - start).total_seconds() * 1000

        return RemReport(
            session_number=session_number,
            timestamp=start.isoformat(),
            operations_run=operations,
            operations_skipped=skipped,
            findings=findings,
            duration_ms=elapsed,
        )

    async def _run_operation(self, operation: str, session_number: int) -> list[RemFinding]:
        """Run a single operation and return its findings."""
        # A table and not an if/elif chain. Python reads each `elif` as an If
        # nested in the previous one's else, so a flat seven-way dispatch
        # measured as nesting depth 7 and the eighth operation would have been
        # refused by this project's own complexity gate.
        handler = {
            "staleness_scan": self._staleness_scan,
            "duplicate_detection": self._duplicate_detection,
            "community_detection": self._community_detection,
            "knowledge_extraction": self._knowledge_extraction,
            "intent_calibration": self._intent_calibration,
            "gate_audit_review": self._gate_audit_review,
            "link_suggestions": self._link_suggestions,
        }.get(operation)
        if handler is None:
            # A misspelled name used to fall through to zero findings and then
            # write a rem_state row under the bogus name: rem_run answered
            # "No findings. Knowledge base looks healthy." for a scan that
            # never ran. run() turns this into a visible "{op} failed"
            # finding, and the raise happens before the row is written.
            raise ValueError(f"unknown REM operation: {operation!r}")
        findings = await handler()

        # The findings themselves, not just how many there were.
        #
        # For a long time only the count survived a run, so a cycle that found
        # 105 unused lessons recommended a fix for each and then discarded every
        # one. The next run found the same 105 and discarded them again, and
        # nothing between runs could show the list. The rows replace this
        # operation's previous rows, because the same unused lesson found in
        # four cycles is one problem and not four.
        await self.store.replace_rem_findings(
            project_id=self.project_id,
            operation=operation,
            session_number=session_number,
            findings=findings,
        )

        # Update rem_state
        schedule = self.schedules.get(operation)
        next_session = next_due_session(schedule, session_number) if schedule else None
        await self.store.update_rem_state(
            project_id=self.project_id,
            operation=operation,
            session_number=session_number,
            result={"finding_count": len(findings)},
            next_due=next_session,
        )

        return findings

    async def _staleness_scan(self) -> list[RemFinding]:
        """Lessons retrieval never reaches, then heavily used lessons going stale.

        Worst first, and the unreachable ones first as a group. A cycle on a
        live corpus produced 105 findings, rendered in full, which is a document
        rather than a report, and the whole set was skimmed. Ranking is what
        makes the top of the list the thing to fix.

        A lesson search has never matched is an outright retrieval failure. A
        heavily used lesson that has not been refined is a freshness question.
        The first group is reported first because the two are not equally
        urgent.
        """
        lessons = await self.store.get_all_lessons()
        now = datetime.now(UTC)
        stamps = _query_times()
        return (self._unmatched_findings(lessons, now, stamps)
                + self._stale_findings(lessons, now))

    def _unmatched_findings(self, lessons, now, stamps) -> list[RemFinding]:
        """Lessons search has never matched, most missed opportunities first.

        ``usage_count`` is the right counter here and telemetry is not. It
        counts a MATCH and deliberately excludes a lesson the community bridge
        appended, so zero means the trigger never won, which is the thing a
        rewritten trigger would fix.
        """
        ranked = []
        for lesson in lessons:
            if lesson.usage_count != 0:
                continue
            missed = _queries_since(stamps, lesson.created_at)
            age_days = (now - lesson.created_at).days
            if missed is None:
                if age_days < FALLBACK_AGE_DAYS:
                    continue
                ranked.append((age_days, lesson, None, age_days))
            elif missed >= MIN_QUERY_OPPORTUNITIES:
                ranked.append((missed, lesson, missed, age_days))
        ranked.sort(key=lambda row: -row[0])
        return [self._unmatched_finding(lesson, missed, age)
                for _, lesson, missed, age in ranked]

    def _unmatched_finding(self, lesson, missed, age_days) -> RemFinding:
        chances = (f"{missed} queries have run since it was written and none of "
                   f"them matched it"
                   if missed is not None else
                   f"it is {age_days} days old, and query history could not be "
                   f"read to count its missed chances")
        return RemFinding(
            operation="staleness_scan",
            title=f"Retrieval never reaches: {lesson.id}",
            description=(
                f"Search has never matched '{lesson.id}'. {chances}. "
                f"Trigger: \"{lesson.trigger[:120]}\""
            ),
            options=[
                {"label": "Rewrite the trigger",
                 "description": ("refine_lesson(new_trigger=...) with the words "
                                 "someone would actually type when this applies")},
                {"label": "Delete",
                 "description": "the lesson is not worth making reachable"},
                {"label": "Keep", "description": "leave it as it is for now"},
            ],
            recommended=0,
            metadata={"lesson_id": lesson.id, "age_days": age_days,
                      "missed_opportunities": missed,
                      "trigger": lesson.trigger},
        )

    def _stale_findings(self, lessons, now) -> list[RemFinding]:
        """Heavily used lessons that have not been refined, worst first.

        Worst means uses multiplied by days since the last refinement. A lesson
        returned 700 times and untouched for a year outranks one returned 11
        times, and sorting on either number alone gets that order wrong.
        """
        ranked = []
        for lesson in lessons:
            if lesson.usage_count < 10:
                continue
            staleness = (now - lesson.last_refined).days
            if staleness > 180:
                ranked.append((lesson.usage_count * staleness, lesson, staleness))
        ranked.sort(key=lambda row: -row[0])
        return [
            RemFinding(
                operation="staleness_scan",
                title=f"Heavily used but stale: {lesson.id}",
                description=(
                    f"'{lesson.id}' has been matched {lesson.usage_count} times "
                    f"and has not been refined in {staleness} days. The wording "
                    f"people are being given may no longer be the best one."
                ),
                options=[
                    {"label": "Review and refine",
                     "description": "read it and update what has drifted"},
                    {"label": "Keep", "description": "it is still accurate"},
                ],
                recommended=0,
                metadata={"lesson_id": lesson.id, "usage_count": lesson.usage_count,
                          "stale_days": staleness},
            )
            for _, lesson, staleness in ranked
        ]

    async def _link_suggestions(self) -> list[RemFinding]:
        """Propose links for a lesson that has none.

        An unlinked lesson is unreachable by BOTH retrieval paths. Search can
        miss it on wording, and the community bridge cannot reach it at all,
        because Louvain puts an isolated node in no community. On a live corpus
        38 of the 44 never-matched lessons carried no edges, which is why they
        sat at exactly zero rather than merely low.

        Needs vectors, so it asks for the store through the factory. A failed
        open leaves the other operations running.
        """
        vector_store = await self._resolve_vector_store()
        if vector_store is None:
            return []

        lessons = await self.store.get_all_lessons()
        by_id = {le.id: le for le in lessons}
        parents = {le.parent_id for le in lessons if le.parent_id}
        orphans = [
            le for le in lessons
            if not le.relationships and not le.parent_id and le.id not in parents
        ]

        findings = []
        for lesson in orphans[:MAX_LINK_FINDINGS]:
            neighbours = self._nearest(vector_store, lesson, by_id)
            if neighbours:
                findings.append(_link_finding(lesson, neighbours))

        if len(orphans) > MAX_LINK_FINDINGS:
            findings.append(_overflow_finding(len(orphans)))
        return findings

    def _nearest(self, vector_store, lesson, by_id) -> list[tuple[str, float]]:
        """The closest other lessons by meaning, above the link score floor.

        A candidate whose trigger already collides with this lesson's is left
        out. That pair is a duplicate, which duplicate_detection reports, and
        linking it would paper over the duplication rather than surface it.
        This operation proposed exactly that for two copies of one rule on its
        first run.
        """
        from .data_ops import DEFAULT_TRIGGER_OVERLAP, trigger_overlap

        try:
            hits = vector_store.search(
                f"{lesson.trigger} {lesson.action}",
                limit=LINK_CANDIDATE_POOL, min_score=LINK_MIN_SCORE)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not search for neighbours of {lesson.id}: {exc}")
            return []

        out = []
        for other, score in hits:
            if other == lesson.id:
                continue
            twin = by_id.get(other)
            if twin is not None:
                share, _shared = trigger_overlap(lesson.trigger, twin.trigger)
                if share >= DEFAULT_TRIGGER_OVERLAP:
                    continue
            out.append((other, score))
        return out[:LINKS_PER_LESSON]

    async def _resolve_vector_store(self):
        """The vector store, opened now if the caller gave a way to open it.

        Called only from the one operation that needs vectors. The result is
        remembered, including a None, so a failed open is not retried inside the
        same cycle.
        """
        if self.vector_store is None and self._vector_store_factory and not self._factory_called:
            self._factory_called = True
            try:
                self.vector_store = await self._vector_store_factory()
            except Exception as exc:
                # The caller's factory reports its own reasons. A failure here
                # must not stop the six operations that need no vectors.
                logger.warning(f"Could not open the vector store for REM: {exc}")
                self.vector_store = None
        return self.vector_store

    async def _duplicate_detection(self) -> list[RemFinding]:
        """Find lessons that compete for the same retrieval.

        Ranked by trigger overlap and NOT by semantic similarity, which cannot
        answer this. Two lessons carrying the same rule in different words
        scored 0.739 against the old 0.85 gate, so they were never reported and
        sat in the store for nine months. By trigger overlap that pair ranked
        6th of 1,537.

        It needs no vector store now, so a duplicate scan no longer takes the
        Qdrant lock. The similarity column went with the gate.
        """
        from .data_ops import find_duplicates

        try:
            pairs = await find_duplicates(store=self.store)
        except Exception as e:
            # Returning [] here read as "no duplicates found", which the report
            # renders as "Knowledge base looks healthy" — a clean bill of health
            # from a scan that never ran. Say so instead.
            logger.warning(f"Duplicate detection failed: {e}")
            return [RemFinding(
                operation="duplicate_detection",
                title="Duplicate scan could not run",
                description=(
                    f"find_duplicates raised: {e}\n\n"
                    "This says nothing about duplicates. The scan did not "
                    "complete, so the corpus is unverified. Run `mgcp-duplicates` "
                    "out of process to check."
                ),
                options=[
                    {"label": "Acknowledge", "description": "Investigate separately"},
                ],
                recommended=0,
                metadata={"error": str(e)},
            )]

        return [_duplicate_finding(pair) for pair in pairs]

    async def _community_detection(self) -> list[RemFinding]:
        """Detect topic clusters and suggest linking orphans."""
        from .graph import LessonGraph

        graph = LessonGraph()
        lessons = await self.store.get_all_lessons()

        if len(lessons) < 3:
            return []

        for lesson in lessons:
            graph.add_lesson(lesson)

        communities = graph.detect_communities()
        findings = []

        # A lesson that clustered with nothing comes back as a singleton
        # community — that is the only way Louvain can say "did not cluster".
        # This used to subtract the union of all members from all lessons,
        # which is always empty: Louvain partitions every node, so the union
        # is every lesson and the orphan finding could never fire.
        orphans = [c["members"][0] for c in communities if c.get("size") == 1]

        if orphans:
            orphan_ids = orphans[:10]  # Cap at 10
            findings.append(RemFinding(
                operation="community_detection",
                title=f"{len(orphans)} orphan lessons with no relationships",
                description=(
                    f"These lessons clustered with nothing — no relationships tie "
                    f"them to the rest of the graph: {', '.join(orphan_ids)}"
                ),
                options=[
                    {"label": "Review & link", "description": "Suggest relationships for these lessons"},
                    {"label": "Skip", "description": "They're standalone"},
                ],
                recommended=0,
                metadata={"orphan_ids": orphan_ids},
            ))

        # Nothing here compares this run against the last, so this reports the
        # current cluster count, not a change in it.
        if communities:
            findings.append(RemFinding(
                operation="community_detection",
                title=f"Detected {len(communities)} topic clusters",
                description=(
                    f"Communities found with {sum(c.get('size', 0) for c in communities)} "
                    f"total lessons across {len(communities)} clusters."
                ),
                options=[
                    {"label": "Update summaries", "description": "Generate/update community summaries"},
                    {"label": "Skip", "description": "Existing summaries are good enough"},
                ],
                recommended=0,
                metadata={"community_count": len(communities)},
            ))

        return findings

    async def _knowledge_extraction(self) -> list[RemFinding]:
        """Extract patterns from context history."""

        # Get all projects and scan their recent history
        projects = await self.store.get_all_project_contexts()
        findings = []

        for project in projects:
            history = await self.store.get_context_history(project.project_id, limit=20)
            if len(history) < 3:
                continue

            # Flag projects carrying 3+ pending todos. This used to skip the
            # whole project when no snapshot carried a note, gated on a list of
            # notes nothing read — the todo and decision checks below do not
            # depend on notes.
            latest = history[0]
            if latest.get("todos"):
                try:
                    todos = json.loads(latest["todos"])
                    stale_todos = [
                        t for t in todos
                        if t.get("status") == "pending"
                    ]
                    if len(stale_todos) >= 3:
                        findings.append(RemFinding(
                            operation="knowledge_extraction",
                            title=f"{len(stale_todos)} pending todos in {project.project_name}",
                            description=(
                                f"Project '{project.project_name}' has {len(stale_todos)} pending "
                                f"todos that may need attention or cleanup."
                            ),
                            options=[
                                {"label": "Review todos", "description": "Check which are still relevant"},
                                {"label": "Skip", "description": "They're intentionally deferred"},
                            ],
                            recommended=0,
                            metadata={
                                "project_id": project.project_id,
                                "pending_count": len(stale_todos),
                            },
                        ))
                except (json.JSONDecodeError, TypeError):
                    pass

            # Check for decisions that could become lessons
            if latest.get("recent_decisions"):
                try:
                    decisions = json.loads(latest["recent_decisions"])
                    if len(decisions) >= 2:
                        findings.append(RemFinding(
                            operation="knowledge_extraction",
                            title=f"Uncaptured decisions in {project.project_name}",
                            description=(
                                f"Project has {len(decisions)} recent decisions that may be "
                                f"worth capturing as lessons:\n"
                                + "\n".join(f"- {d}" for d in decisions[:5])
                            ),
                            options=[
                                {"label": "Create lessons", "description": "Turn decisions into reusable lessons"},
                                {"label": "Skip", "description": "These are project-specific"},
                            ],
                            recommended=0,
                            metadata={"project_id": project.project_id, "decisions": decisions[:5]},
                        ))
                except (json.JSONDecodeError, TypeError):
                    pass

        return findings

    async def _gate_audit_review(self) -> list[RemFinding]:
        """Sample the enforcement gate's audit log for human review.

        The attest-or-comply gate (v2.11) lets the agent contest a fire on
        the record instead of being hard-blocked. That design is only
        honest if somebody reads the record. This operation is that
        somebody's assistant: it summarizes the fires, compliances,
        adjudications and human bypasses in the recent tail of the log,
        surfaces every contested verdict with its reasoning, and flags a
        contest rate that suggests the gate is being talked around.
        """
        import json as _json
        import os as _os
        from pathlib import Path as _Path

        audit_path = _Path(
            _os.environ.get("MGCP_DATA_DIR", str(_Path.home() / ".mgcp"))
        ) / "gate_audit.jsonl"
        if not audit_path.exists():
            return []

        events = []
        try:
            for line in audit_path.read_text().splitlines()[-200:]:
                try:
                    events.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
        except OSError:
            return []
        if not events:
            return []

        denies = [e for e in events if e.get("event") == "deny"]
        gate_denies = [e for e in denies if e.get("gate") == "apology"]
        complies = [e for e in events if e.get("event") == "comply"]
        contests = [e for e in events
                    if e.get("event") == "adjudication"
                    and e.get("verdict") == "not_apology"]
        confirms = [e for e in events
                    if e.get("event") == "adjudication"
                    and e.get("verdict") == "apology"]
        bypasses = [e for e in events if e.get("event") == "human_bypass"]
        # A hook_error is the PreToolUse gate crashing and failing open: the
        # one event that means enforcement was skipped. Left out of `known` it
        # was filed as "unrecognised type", which buries it.
        hook_errors = [e for e in events if e.get("event") == "hook_error"]
        known = {"deny", "comply", "adjudication", "human_bypass", "hook_error"}
        unknown = [e for e in events if e.get("event") not in known]

        findings = []
        description = (
            f"Last {len(events)} audit events: {len(gate_denies)} apology-gate "
            f"denial(s) ({len(denies) - len(gate_denies)} from data rules), "
            f"{len(complies)} compliance(s), {len(contests)} contested / "
            f"{len(confirms)} confirmed adjudication(s), "
            f"{len(bypasses)} human bypass(es), "
            f"{len(hook_errors)} hook fail-open(s)."
        )
        if unknown:
            # A future event type must be REPORTED, not silently dropped: the
            # audit log is only an instrument if it surfaces what it does not
            # recognise.
            kinds = sorted({str(e.get("event")) for e in unknown})
            description += (
                f" NOTE: {len(unknown)} event(s) of unrecognised type "
                f"{kinds} — this reviewer does not summarise them; read the "
                "log directly."
            )
        if contests:
            sample = contests[-5:]
            description += " Contested verdicts to review: " + " | ".join(
                f"[{c.get('flagged_sentence', '')!r} -> {c.get('reasoning', '')!r}]"
                for c in sample
            )
        findings.append(RemFinding(
            operation="gate_audit_review",
            title=f"Gate audit: {len(gate_denies)} fires, {len(contests)} contested",
            description=description,
            options=[
                {"label": "Review contests", "description": "Read each contested verdict against its flagged sentence"},
                {"label": "Acknowledge", "description": "Counts noted, nothing suspicious"},
            ],
            recommended=0 if contests else 1,
            metadata={
                "fires": len(gate_denies), "complies": len(complies),
                "contests": len(contests), "confirms": len(confirms),
                "bypasses": len(bypasses), "rule_denies": len(denies) - len(gate_denies),
                "hook_errors": len(hook_errors),
            },
        ))
        if len(contests) >= 3 and len(contests) * 2 >= max(len(gate_denies), 1):
            findings.append(RemFinding(
                operation="gate_audit_review",
                title="High contest rate on the apology gate",
                description=(
                    f"{len(contests)} of {len(gate_denies)} recent fires were "
                    "contested. Either the tripwires are noisy (widen review) "
                    "or the agent is talking its way past the gate (read the "
                    "reasonings)."
                ),
                options=[
                    {"label": "Audit reasonings", "description": "Human reads gate_audit.jsonl"},
                    {"label": "Tune tripwires", "description": "Remove patterns producing false fires"},
                ],
                recommended=0,
                metadata={"contest_rate": round(len(contests) / max(len(gate_denies), 1), 2)},
            ))
        return findings

    async def _intent_calibration(self) -> list[RemFinding]:
        """Compare community structure against the configured intent set.

        Surfaces two failure modes that indicate the intent config needs
        updating:

        1. **Unmapped tags** — a community contains tags that don't map to
           any intent at all. Means the config needs new tag entries or a
           new intent.

        2. **Incoherent community** — the community's tags map across
           multiple intents and the dominant intent has < 60% share. Means
           either (a) the community is semantically distinct and deserves
           its own intent, or (b) tags were mis-assigned and need to be
           moved between intents.

        The previous implementation only had check #1, and worse, the tag
        map was hand-maintained. Defensive over-mapping (e.g. shoving
        ``session-discipline`` and ``session`` into ``task_start``) silenced
        check #1 even when the community was semantically a misfit. Adding
        coherence as a separate signal catches that case.

        Both findings include a structured ``proposed_patch`` in metadata so
        a future writeback path (CLI tool or MCP tool) can apply REM's
        recommendation directly to ``intent_config.json``.
        """
        from .graph import LessonGraph
        from .intent_config import load_config

        config = load_config()
        tag_to_intent = config.tag_to_intent()

        graph = LessonGraph()
        lessons = await self.store.get_all_lessons()

        if len(lessons) < 5:
            return []

        for lesson in lessons:
            graph.add_lesson(lesson)

        communities = graph.detect_communities()
        findings: list[RemFinding] = []

        for comm in communities:
            tags = list(comm.get("aggregate_tags", {}).keys())
            if not tags:
                continue
            size = comm.get("size", 0)
            if size < 3:
                continue

            # Compute intent distribution for this community
            intent_counts: dict[str, int] = {}
            unmapped: list[str] = []
            for t in tags:
                intent = tag_to_intent.get(t.lower())
                if intent is None:
                    unmapped.append(t)
                else:
                    intent_counts[intent] = intent_counts.get(intent, 0) + 1

            total_mapped = sum(intent_counts.values())
            dominant_intent = (
                max(intent_counts, key=intent_counts.get) if intent_counts else None
            )
            dominant_share = (
                intent_counts[dominant_intent] / total_mapped
                if dominant_intent and total_mapped
                else 0.0
            )

            comm_label = comm.get("label") or comm["community_id"]
            top_members = comm.get("top_members", [])

            # --- Finding 1: unmapped tags ---
            if unmapped:
                findings.append(RemFinding(
                    operation="intent_calibration",
                    title=f"Unmapped tags in community '{comm_label}': {', '.join(sorted(unmapped))}",
                    description=(
                        f"Community '{comm_label}' ({size} lessons) has tags not "
                        f"mapped to any intent: {sorted(unmapped)}. "
                        f"Dominant intent so far: {dominant_intent} "
                        f"({dominant_share:.0%} of mapped tags). "
                        f"Members: {', '.join(top_members)}"
                    ),
                    options=[
                        {
                            "label": "Map to dominant",
                            "description": (
                                f"Add {sorted(unmapped)} to the '{dominant_intent}' intent's tags"
                                if dominant_intent
                                else "No dominant intent — create a new one"
                            ),
                        },
                        {
                            "label": "Add new intent",
                            "description": "Create a new intent for these tags",
                        },
                        {"label": "Skip", "description": "Not actionable"},
                    ],
                    recommended=0 if dominant_intent else 1,
                    metadata={
                        "community_id": comm["community_id"],
                        "community_label": comm_label,
                        "unmapped_tags": sorted(unmapped),
                        "dominant_intent": dominant_intent,
                        "dominant_share": round(dominant_share, 3),
                        "size": size,
                        "top_members": top_members,
                        "proposed_patch": {
                            "type": "add_tags_to_intent",
                            "intent": dominant_intent,
                            "tags": sorted(unmapped),
                        }
                        if dominant_intent
                        else {
                            "type": "create_intent",
                            "suggested_tags": sorted(unmapped),
                        },
                    },
                ))

            # --- Finding 2: incoherent community ---
            #
            # If a community's tags spread across multiple intents and the
            # dominant intent doesn't dominate (< 60%), the community is
            # likely a misfit cluster that deserves its own intent. This is
            # the check that *would* have caught the missing session_end
            # intent: lessons tagged with session-discipline + farewell +
            # save-context all got force-mapped to task_start by the old
            # hand-coded dict, hiding the fact that they belonged to a
            # distinct lifecycle intent.
            if (
                dominant_intent
                and dominant_share < 0.60
                and len(intent_counts) >= 2
            ):
                sorted_dist = dict(
                    sorted(intent_counts.items(), key=lambda x: -x[1])
                )
                findings.append(RemFinding(
                    operation="intent_calibration",
                    title=f"Incoherent community '{comm_label}' spans {len(intent_counts)} intents",
                    description=(
                        f"Community '{comm_label}' ({size} lessons) has tags spread "
                        f"across {len(intent_counts)} intents: {sorted_dist}. "
                        f"Dominant intent {dominant_intent} only covers "
                        f"{dominant_share:.0%} of mapped tags. This usually means "
                        f"a new intent reflecting the community's theme is needed, "
                        f"or some tags should be remapped to improve coherence. "
                        f"Members: {', '.join(top_members)}"
                    ),
                    options=[
                        {
                            "label": "Add new intent",
                            "description": "Create a new intent reflecting this community's theme",
                        },
                        {
                            "label": "Remap tags",
                            "description": "Move some tags between intents to improve coherence",
                        },
                        {
                            "label": "Skip",
                            "description": "Community is genuinely cross-cutting",
                        },
                    ],
                    recommended=0,
                    metadata={
                        "community_id": comm["community_id"],
                        "community_label": comm_label,
                        "intent_distribution": sorted_dist,
                        "dominant_intent": dominant_intent,
                        "dominant_share": round(dominant_share, 3),
                        "size": size,
                        "top_members": top_members,
                        "proposed_patch": {
                            "type": "create_intent",
                            "reason": "incoherent_community",
                            "community_id": comm["community_id"],
                        },
                    },
                ))

        return findings
