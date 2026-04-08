"""REM (Recalibrate Everything in Memory) cycle engine.

Coordinates periodic knowledge consolidation operations:
- Staleness scan: find lessons that may be outdated
- Duplicate detection: find semantically similar lessons
- Community detection: discover topic clusters in the graph
- Knowledge extraction: surface patterns from context history
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

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


class RemEngine:
    """Orchestrates REM cycle operations."""

    def __init__(self, store: LessonStore, schedules: dict[str, OperationSchedule] | None = None):
        self.store = store
        self.schedules = schedules or DEFAULT_SCHEDULES

    async def get_due_operations(self, session_number: int) -> list[str]:
        """Determine which operations are due at this session number."""
        states = await self.store.get_rem_state()
        state_map = {s["operation"]: s["last_run_session"] for s in states}

        due = []
        for op_name, schedule in self.schedules.items():
            last_run = state_map.get(op_name, 0)
            if is_due(schedule, session_number, last_run):
                due.append(op_name)
        return due

    async def get_status(self) -> list[dict]:
        """Get schedule status for all operations."""
        states = await self.store.get_rem_state()
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
                "last_run_timestamp": state["last_run_timestamp"] if state else None,
            }

            if state and state.get("last_run_result"):
                try:
                    entry["last_result_summary"] = json.loads(state["last_run_result"])
                except (json.JSONDecodeError, TypeError):
                    pass

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
        if operation == "staleness_scan":
            findings = await self._staleness_scan()
        elif operation == "duplicate_detection":
            findings = await self._duplicate_detection()
        elif operation == "community_detection":
            findings = await self._community_detection()
        elif operation == "knowledge_extraction":
            findings = await self._knowledge_extraction(session_number)
        elif operation == "context_summary":
            findings = await self._context_summary()
        elif operation == "intent_calibration":
            findings = await self._intent_calibration()
        elif operation == "action_effectiveness":
            findings = await self._action_effectiveness()
        else:
            findings = []

        # Update rem_state
        schedule = self.schedules.get(operation)
        next_session = next_due_session(schedule, session_number) if schedule else None
        await self.store.update_rem_state(
            operation=operation,
            session_number=session_number,
            result={"finding_count": len(findings)},
            next_due=next_session,
        )

        return findings

    async def _staleness_scan(self) -> list[RemFinding]:
        """Find lessons that may be outdated."""
        lessons = await self.store.get_all_lessons()
        now = datetime.now(UTC)
        findings = []

        for lesson in lessons:
            # Never retrieved and older than 30 days
            if lesson.usage_count == 0:
                age = now - lesson.created_at
                if age > timedelta(days=30):
                    findings.append(RemFinding(
                        operation="staleness_scan",
                        title=f"Unused lesson: {lesson.id}",
                        description=(
                            f"Lesson '{lesson.id}' has never been retrieved and was "
                            f"created {age.days} days ago. Trigger: \"{lesson.trigger[:80]}\""
                        ),
                        options=[
                            {"label": "Update trigger", "description": "Rewrite trigger keywords to improve retrieval"},
                            {"label": "Delete", "description": "Remove this lesson entirely"},
                            {"label": "Keep", "description": "Leave as-is for now"},
                        ],
                        recommended=0,
                        metadata={"lesson_id": lesson.id, "age_days": age.days},
                    ))

            # High usage but not refined in 6+ months
            elif lesson.usage_count >= 10:
                staleness = now - lesson.last_refined
                if staleness > timedelta(days=180):
                    findings.append(RemFinding(
                        operation="staleness_scan",
                        title=f"Heavily used but stale: {lesson.id}",
                        description=(
                            f"Lesson '{lesson.id}' has been retrieved {lesson.usage_count} times "
                            f"but hasn't been refined in {staleness.days} days. "
                            f"It may need updating."
                        ),
                        options=[
                            {"label": "Review & refine", "description": "Open this lesson for refinement"},
                            {"label": "Keep", "description": "It's still accurate"},
                        ],
                        recommended=0,
                        metadata={"lesson_id": lesson.id, "usage_count": lesson.usage_count},
                    ))

        return findings

    async def _duplicate_detection(self) -> list[RemFinding]:
        """Find semantically similar lessons."""
        from .data_ops import find_duplicates

        try:
            pairs = await find_duplicates(threshold=0.85)
        except Exception as e:
            logger.warning(f"Duplicate detection failed: {e}")
            return []

        findings = []
        for pair in pairs:
            similarity = pair.get("similarity", 0)
            id_a = pair.get("lesson_a", "?")
            id_b = pair.get("lesson_b", "?")

            findings.append(RemFinding(
                operation="duplicate_detection",
                title=f"Potential duplicates ({similarity:.0%}): {id_a} / {id_b}",
                description=(
                    f"Lessons '{id_a}' and '{id_b}' have {similarity:.0%} semantic similarity.\n\n"
                    f"A: {pair.get('trigger_a', '')[:100]}\n"
                    f"B: {pair.get('trigger_b', '')[:100]}"
                ),
                options=[
                    {"label": "Merge", "description": "Combine into one lesson"},
                    {"label": "Keep both", "description": "They're different enough"},
                    {"label": "Delete one", "description": "Remove the weaker lesson"},
                ],
                recommended=0 if similarity > 0.92 else 1,
                metadata={"lesson_a": id_a, "lesson_b": id_b, "similarity": similarity},
            ))

        return findings

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

        # Find orphan lessons (not in any community)
        all_members = set()
        for comm in communities:
            all_members.update(comm.get("members", []))

        orphans = [l for l in lessons if l.id not in all_members and len(l.relationships) == 0]

        if orphans:
            orphan_ids = [o.id for o in orphans[:10]]  # Cap at 10
            findings.append(RemFinding(
                operation="community_detection",
                title=f"{len(orphans)} orphan lessons with no relationships",
                description=(
                    f"These lessons have no relationships to other lessons and didn't "
                    f"cluster into any community: {', '.join(orphan_ids)}"
                ),
                options=[
                    {"label": "Review & link", "description": "Suggest relationships for these lessons"},
                    {"label": "Skip", "description": "They're standalone"},
                ],
                recommended=0,
                metadata={"orphan_ids": orphan_ids},
            ))

        # Report new/changed communities
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
                    {"label": "Skip", "description": "Communities haven't changed significantly"},
                ],
                recommended=0,
                metadata={"community_count": len(communities)},
            ))

        return findings

    async def _knowledge_extraction(self, session_number: int) -> list[RemFinding]:
        """Extract patterns from context history."""

        # Get all projects and scan their recent history
        projects = await self.store.get_all_project_contexts()
        findings = []

        for project in projects:
            history = await self.store.get_context_history(project.project_id, limit=20)
            if len(history) < 3:
                continue

            # Look for recurring themes in notes
            all_notes = [h["notes"] for h in history if h.get("notes")]
            if not all_notes:
                continue

            # Find stale todos (pending for 5+ sessions)
            latest = history[0] if history else None
            if latest and latest.get("todos"):
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
                                "stale_count": len(stale_todos),
                            },
                        ))
                except (json.JSONDecodeError, TypeError):
                    pass

            # Check for decisions that could become lessons
            if latest and latest.get("recent_decisions"):
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

    async def _context_summary(self) -> list[RemFinding]:
        """Summarize context history into compressed narratives."""
        projects = await self.store.get_all_project_contexts()
        findings = []

        for project in projects:
            history = await self.store.get_context_history(project.project_id, limit=50)
            if len(history) < 10:
                continue

            findings.append(RemFinding(
                operation="context_summary",
                title=f"Context history available for {project.project_name}",
                description=(
                    f"Project '{project.project_name}' has {len(history)} history snapshots "
                    f"spanning sessions. A summary could compress older entries into narratives "
                    f"while preserving key transitions."
                ),
                options=[
                    {"label": "Generate summary", "description": "Compress older history into narrative"},
                    {"label": "Skip", "description": "Keep raw history for now"},
                ],
                recommended=0,
                metadata={"project_id": project.project_id, "snapshot_count": len(history)},
            ))

        return findings

    async def capture_lesson_baseline(self, lesson_id: str) -> dict:
        """Capture a snapshot of lesson metrics for before/after comparison."""
        lesson = await self.store.get_lesson(lesson_id)
        if not lesson:
            return {"lesson_id": lesson_id, "error": "not_found"}
        return {
            "lesson_id": lesson_id,
            "usage_count": lesson.usage_count,
            "version": lesson.version,
            "trigger": lesson.trigger,
            "tags": lesson.tags,
            "last_used": lesson.last_used.isoformat() if lesson.last_used else None,
            "captured_at": datetime.now(UTC).isoformat(),
        }

    async def _action_effectiveness(self) -> list[RemFinding]:
        """Measure outcomes of past REM actions.

        Compares current lesson metrics against baseline snapshots
        to determine if remediation actions improved, maintained, or
        degraded lesson effectiveness.
        """
        unmeasured = await self.store.get_unmeasured_actions(min_age_days=7)
        if not unmeasured:
            return []

        findings = []
        measured_count = 0
        verdicts = {"improved": 0, "unchanged": 0, "degraded": 0, "target_gone": 0}

        for action in unmeasured:
            if action.id is None:
                continue

            baseline = action.baseline_snapshot
            target_id = action.target_id

            if action.target_type == "lesson":
                lesson = await self.store.get_lesson(target_id)
                if lesson is None:
                    # Lesson was deleted — expected for delete actions
                    if action.action_type == "delete":
                        verdict = "improved"
                    else:
                        verdict = "target_gone"
                    outcome = {
                        "verdict": verdict,
                        "reason": "lesson no longer exists",
                    }
                else:
                    baseline_usage = baseline.get("usage_count", 0)
                    current_usage = lesson.usage_count
                    delta = current_usage - baseline_usage

                    if delta > 0:
                        verdict = "improved"
                    elif delta == 0:
                        # Check if trigger was updated and lesson still isn't used
                        if action.action_type == "trigger_update":
                            verdict = "unchanged"
                        else:
                            verdict = "unchanged"
                    else:
                        verdict = "degraded"

                    outcome = {
                        "verdict": verdict,
                        "usage_before": baseline_usage,
                        "usage_after": current_usage,
                        "delta": delta,
                        "version_before": baseline.get("version", 1),
                        "version_after": lesson.version,
                    }

                verdicts[verdict] = verdicts.get(verdict, 0) + 1
                await self.store.measure_action(action.id, outcome)
                measured_count += 1

            elif action.target_type == "intent_tag":
                # Intent mappings: just mark as measured (hard to measure directly)
                outcome = {"verdict": "unchanged", "reason": "intent mappings measured indirectly"}
                verdicts["unchanged"] += 1
                await self.store.measure_action(action.id, outcome)
                measured_count += 1

        if measured_count > 0:
            findings.append(RemFinding(
                operation="action_effectiveness",
                title=f"Measured {measured_count} past actions",
                description=(
                    f"Results: {verdicts['improved']} improved, "
                    f"{verdicts['unchanged']} unchanged, "
                    f"{verdicts['degraded']} degraded, "
                    f"{verdicts['target_gone']} targets gone."
                ),
                options=[
                    {"label": "Review details", "description": "Inspect individual action outcomes"},
                    {"label": "Acknowledge", "description": "Results noted"},
                ],
                recommended=1,
                metadata={
                    "measured_count": measured_count,
                    "verdicts": verdicts,
                },
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
