"""Tests for REM cycle engine."""

from datetime import UTC, datetime, timedelta

import pytest

from mgcp.models import Lesson, ProjectContext, ProjectTodo
from mgcp.persistence import LessonStore
from mgcp.rem_config import OperationSchedule
from mgcp.rem_cycle import RemEngine, RemFinding


@pytest.fixture
def store(temp_db):
    return LessonStore(temp_db)


PROJECT = "test-project"


@pytest.fixture
def engine(store):
    """Create a REM engine with short intervals for testing."""
    schedules = {
        "staleness_scan": OperationSchedule(strategy="linear", interval=2),
        "knowledge_extraction": OperationSchedule(strategy="linear", interval=3),
    }
    return RemEngine(store=store, schedules=schedules, project_id=PROJECT)


class TestRemEngineScheduling:
    """Test that the engine correctly determines what's due."""

    @pytest.mark.asyncio
    async def test_all_due_on_first_run(self, engine):
        due = await engine.get_due_operations(session_number=5)
        assert "staleness_scan" in due
        assert "knowledge_extraction" in due

    @pytest.mark.asyncio
    async def test_respects_schedule_after_run(self, engine, store):
        # Run staleness at session 5
        await store.update_rem_state(PROJECT, "staleness_scan", session_number=5, next_due=7)

        # At session 6, staleness shouldn't be due yet (interval=2)
        due = await engine.get_due_operations(session_number=6)
        assert "staleness_scan" not in due

        # At session 7, it should be due
        due = await engine.get_due_operations(session_number=7)
        assert "staleness_scan" in due

    @pytest.mark.asyncio
    async def test_get_status(self, engine, store):
        await store.update_rem_state(PROJECT, "staleness_scan", session_number=10)
        status = await engine.get_status(session_number=10)
        assert len(status) == 2  # Our test engine has 2 operations

        staleness = next(s for s in status if s["operation"] == "staleness_scan")
        assert staleness["last_run_session"] == 10
        assert staleness["strategy"] == "linear"

    @pytest.mark.asyncio
    async def test_status_is_due_matches_due_operations(self, engine, store):
        """What rem_status displays and what rem_run executes cannot disagree.

        Previously rem_status re-derived due-ness as
        ``current >= next_due_session`` while rem_run used ``is_due()``.
        Session 11 with staleness last run at 10 is the case that splits them:
        next_due_session is 10 (last multiple boundary), so the old display
        said DUE while the engine skipped it.
        """
        await store.update_rem_state(PROJECT, "staleness_scan", session_number=10)

        for session in range(1, 20):
            status = await engine.get_status(session_number=session)
            displayed = {s["operation"] for s in status if s["is_due"]}
            executed = set(await engine.get_due_operations(session_number=session))
            assert displayed == executed, f"disagreement at session {session}"


class TestStalenessScan:
    """Test the staleness scan operation."""

    @pytest.mark.asyncio
    async def test_finds_unused_old_lessons(self, store, engine, no_query_history):
        """With no query history to read, age is the only signal left.

        The sandbox has no telemetry.db, so the scan cannot count missed
        opportunities and falls back to age. That fallback is the subject here;
        the opportunity path has its own tests below.
        """
        old_lesson = Lesson(
            id="unused-old",
            trigger="some old trigger",
            action="Some action",
            tags=["test"],
            created_at=datetime.now(UTC) - timedelta(days=45),
            usage_count=0,
        )
        await store.add_lesson(old_lesson)

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        stale = [f for f in report.findings if f.metadata.get("lesson_id") == "unused-old"]
        assert len(stale) == 1
        assert "has never matched" in stale[0].description

    @pytest.mark.asyncio
    async def test_ignores_recently_created(self, store, engine, no_query_history):
        """Nothing has had a chance to retrieve it yet, so it is not a defect."""
        new_lesson = Lesson(
            id="unused-new",
            trigger="new trigger",
            action="New action",
            tags=["test"],
            usage_count=0,
        )
        await store.add_lesson(new_lesson)

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        stale = [f for f in report.findings if f.metadata.get("lesson_id") == "unused-new"]
        assert len(stale) == 0

    @pytest.mark.asyncio
    async def test_finds_heavily_used_but_stale(self, store, engine, no_query_history):
        """High-usage lessons not refined in 6+ months should be flagged."""
        stale_lesson = Lesson(
            id="popular-stale",
            trigger="popular trigger",
            action="Popular action",
            tags=["test"],
            usage_count=15,
            last_refined=datetime.now(UTC) - timedelta(days=200),
        )
        await store.add_lesson(stale_lesson)

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        stale = [f for f in report.findings if f.metadata.get("lesson_id") == "popular-stale"]
        assert len(stale) == 1
        assert "has not been refined" in stale[0].description


class TestKnowledgeExtraction:
    """Test the knowledge extraction operation."""

    @pytest.mark.asyncio
    async def test_finds_stale_todos(self, store, engine):
        """Projects with many pending todos should be flagged."""
        import hashlib
        path = "/test/project"
        ctx = ProjectContext(
            project_id=hashlib.sha256(path.encode()).hexdigest()[:12],
            project_name="Test Project",
            project_path=path,
            session_count=10,
            notes="Working on feature X",
            todos=[
                ProjectTodo(content="Task 1", status="pending"),
                ProjectTodo(content="Task 2", status="pending"),
                ProjectTodo(content="Task 3", status="pending"),
            ],
            recent_decisions=["Decision A", "Decision B"],
        )
        await store.save_project_context(ctx)

        # Need at least 3 history entries for extraction to run
        ctx.session_count = 11
        ctx.notes = "Still on feature X"
        await store.save_project_context(ctx)
        ctx.session_count = 12
        ctx.notes = "Feature X nearly done"
        await store.save_project_context(ctx)

        report = await engine.run(session_number=12, operations=["knowledge_extraction"])
        # Should find stale todos and/or uncaptured decisions
        finding_types = [f.title for f in report.findings]
        assert len(report.findings) > 0, f"Expected findings but got: {finding_types}"


class TestRemReport:
    """Test report generation."""

    @pytest.mark.asyncio
    async def test_report_tracks_operations(self, engine):
        report = await engine.run(session_number=5)
        assert "staleness_scan" in report.operations_run
        assert report.session_number == 5
        assert report.timestamp is not None
        assert report.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_specific_operations(self, engine):
        report = await engine.run(session_number=5, operations=["staleness_scan"])
        assert report.operations_run == ["staleness_scan"]
        assert "knowledge_extraction" in report.operations_skipped

    @pytest.mark.asyncio
    async def test_updates_rem_state(self, engine, store):
        await engine.run(session_number=10, operations=["staleness_scan"])
        states = await store.get_rem_state(PROJECT)
        assert len(states) == 1
        assert states[0]["operation"] == "staleness_scan"
        assert states[0]["last_run_session"] == 10


class TestDuplicateDetection:
    """_duplicate_detection must read find_duplicates' nested pair shape."""

    @pytest.mark.asyncio
    async def test_renders_real_ids_from_nested_pairs(self, engine, monkeypatch):
        captured = {}

        async def fake_find_duplicates(threshold=0.85, store=None, vector_store=None):
            # The engine must hand its own live instances down: local Qdrant
            # permits one client per path, so constructing a second one inside
            # the server raised and the failure was swallowed as "healthy".
            captured["store"] = store
            return [{
                "lesson_1": {"id": "lesson-one", "trigger": "trigger one"},
                "lesson_2": {"id": "lesson-two", "trigger": "trigger two"},
                "similarity": 0.93,
            }]

        import mgcp.data_ops
        monkeypatch.setattr(mgcp.data_ops, "find_duplicates", fake_find_duplicates)

        findings = await engine._duplicate_detection()
        assert len(findings) == 1
        f = findings[0]
        assert "lesson-one" in f.title and "lesson-two" in f.title
        assert "?" not in f.title
        assert "trigger one" in f.description
        # session-detail.html reads these metadata keys; they are load-bearing
        assert f.metadata["lesson_a"] == "lesson-one"
        assert f.metadata["lesson_b"] == "lesson-two"
        assert captured["store"] is engine.store, (
            "engine must pass its own store rather than letting find_duplicates "
            "open a second Qdrant client on the same path"
        )

    @pytest.mark.asyncio
    async def test_scan_failure_is_reported_not_swallowed(self, engine, monkeypatch):
        """A scan that cannot run must not read as a clean corpus."""
        async def boom(threshold=0.85, store=None, vector_store=None):
            raise RuntimeError("already accessed by another instance of Qdrant client")

        import mgcp.data_ops
        monkeypatch.setattr(mgcp.data_ops, "find_duplicates", boom)

        findings = await engine._duplicate_detection()
        assert len(findings) == 1, "failure must surface as a finding, not []"
        assert "could not run" in findings[0].title
        assert "Qdrant" in findings[0].metadata["error"]


class TestRemFinding:
    """Test finding structure."""

    def test_finding_has_options(self):
        f = RemFinding(
            operation="test",
            title="Test finding",
            description="A test",
            options=[
                {"label": "Option A", "description": "Do A"},
                {"label": "Option B", "description": "Do B"},
            ],
            recommended=0,
        )
        assert len(f.options) == 2
        assert f.options[f.recommended]["label"] == "Option A"


class TestGateAuditReview:
    """The v2.11 audit-review operation: the attest-or-comply design is only
    honest if somebody reads the record; this operation is that somebody's
    assistant."""

    def _seed(self, monkeypatch, tmp_path, events):
        import json as j
        monkeypatch.setenv("MGCP_DATA_DIR", str(tmp_path))
        (tmp_path / "gate_audit.jsonl").write_text(
            "\n".join(j.dumps(e) for e in events)
        )

    @pytest.mark.asyncio
    async def test_summarizes_fires_complies_and_contests(self, store, monkeypatch, tmp_path):
        self._seed(monkeypatch, tmp_path, [
            {"event": "deny", "gate": "apology", "tier": "apology-regex", "matched": "sorry"},
            {"event": "comply", "gate": "apology", "lesson_id": "x"},
            {"event": "adjudication", "gate": "apology", "verdict": "not_apology",
             "flagged_sentence": "The user said sorry.", "reasoning": "quoted speech, not my own apology"},
            {"event": "deny", "gate": "rules", "rules": ["git-requires-query-lessons"]},
            {"event": "human_bypass", "scopes": ["apology"]},
        ])
        engine = RemEngine(store=store, schedules={}, project_id=PROJECT)
        findings = await engine._gate_audit_review()
        assert len(findings) == 1
        meta = findings[0].metadata
        assert (meta["fires"], meta["complies"], meta["contests"], meta["bypasses"], meta["rule_denies"]) == (1, 1, 1, 1, 1)
        assert "quoted speech" in findings[0].description

    @pytest.mark.asyncio
    async def test_high_contest_rate_raises_second_finding(self, store, monkeypatch, tmp_path):
        events = []
        for i in range(4):
            events.append({"event": "deny", "gate": "apology", "tier": "apology-regex", "matched": "sorry"})
            events.append({"event": "adjudication", "gate": "apology", "verdict": "not_apology",
                           "flagged_sentence": f"s{i}", "reasoning": "r" * 25})
        self._seed(monkeypatch, tmp_path, events)
        engine = RemEngine(store=store, schedules={}, project_id=PROJECT)
        findings = await engine._gate_audit_review()
        assert len(findings) == 2
        assert "contest rate" in findings[1].title.lower()

    @pytest.mark.asyncio
    async def test_no_audit_file_is_silent(self, store, monkeypatch, tmp_path):
        monkeypatch.setenv("MGCP_DATA_DIR", str(tmp_path))
        engine = RemEngine(store=store, schedules={}, project_id=PROJECT)
        assert await engine._gate_audit_review() == []


def _telemetry_path():
    import os
    from pathlib import Path

    return Path(os.environ["MGCP_DATA_DIR"]) / "telemetry.db"


def _seed_query_history(moments: list[datetime]) -> None:
    """REPLACE the sandbox query history with exactly these query times.

    The scan counts retrieval OPPORTUNITIES, which means queries that ran while
    a lesson already existed. That cannot be faked with a lesson's age, so these
    tests write real rows.

    It replaces rather than appends on purpose. The sandbox data directory is
    shared for the whole test session, so an appending helper made each test
    inherit the rows of the one before it and the suite passed or failed on
    collection order.
    """
    import sqlite3

    path = _telemetry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP TABLE IF EXISTS events")
        conn.execute(
            "CREATE TABLE events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, "
            "session_id TEXT NOT NULL, event_type TEXT NOT NULL, payload JSON)"
        )
        conn.executemany(
            "INSERT INTO events VALUES (?, ?, 's', 'query', '{}')",
            [(f"q{i}", moment.isoformat()) for i, moment in enumerate(moments)],
        )
        conn.commit()
    finally:
        conn.close()


def _queries_spread(count: int, start: datetime, end: datetime) -> list[datetime]:
    """`count` query times spaced evenly between two moments."""
    span = (end - start) / max(1, count)
    return [start + span * (i + 1) for i in range(count)]


@pytest.fixture
def no_query_history():
    """No telemetry before or after, so one test cannot seed another.

    Without this the file survives the test that wrote it, and every later
    lesson in the session inherits its query count.
    """
    _telemetry_path().unlink(missing_ok=True)
    yield
    _telemetry_path().unlink(missing_ok=True)


class TestUnreachableLessonsAreRankedByMissedChances:
    """A lesson search never matches is a defect from the day it is written.

    The test this replaces was "created more than 30 days ago", which hid 30 of
    44 never-matched lessons on a live corpus behind a calendar that says
    nothing about whether anything tried to find them. What matters is how many
    queries ran while the lesson existed and chose something else.
    """

    async def _lesson(self, store, lesson_id, age_days, usage=0):
        lesson = Lesson(
            id=lesson_id, trigger=f"{lesson_id} trigger", action="Do the thing",
            tags=["test"], usage_count=usage,
            created_at=datetime.now(UTC) - timedelta(days=age_days),
        )
        await store.add_lesson(lesson)
        return lesson

    @pytest.mark.asyncio
    async def test_a_young_lesson_with_many_missed_chances_is_flagged(
        self, store, engine, no_query_history
    ):
        """Age would have skipped this one. Opportunity catches it."""
        created = datetime.now(UTC) - timedelta(days=1)
        await self._lesson(store, "young-and-unreachable", age_days=1)
        _seed_query_history(_queries_spread(40, created, datetime.now(UTC)))

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        hit = [f for f in report.findings
               if f.metadata.get("lesson_id") == "young-and-unreachable"]
        assert len(hit) == 1, "a one-day-old lesson with 40 missed queries was skipped"
        assert hit[0].metadata["missed_opportunities"] >= 20
        assert "40 queries have run" in hit[0].description

    @pytest.mark.asyncio
    async def test_too_few_chances_is_not_flagged(
        self, store, engine, no_query_history
    ):
        """Under the threshold nothing is proven, so nothing is claimed."""
        created = datetime.now(UTC) - timedelta(days=1)
        await self._lesson(store, "barely-asked", age_days=1)
        _seed_query_history(_queries_spread(5, created, datetime.now(UTC)))

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        assert not [f for f in report.findings
                    if f.metadata.get("lesson_id") == "barely-asked"]

    @pytest.mark.asyncio
    async def test_most_missed_chances_comes_first(
        self, store, engine, no_query_history
    ):
        """Worst first. One run on a live corpus produced 133 findings.

        60 queries spread over ten days, so the ten-day-old lesson missed all
        60 and the five-day-old one missed about half. Both clear the threshold,
        which is what makes this a test of ORDER and not of the threshold.
        """
        now = datetime.now(UTC)
        # Inserted newest FIRST on purpose. get_all_lessons orders by
        # usage_count, which is 0 for both, so the store hands them back in
        # insertion order. Adding the older one first made this test pass with
        # the sort deleted, which is a test of nothing.
        await self._lesson(store, "newer-so-fewer-missed", age_days=5)
        await self._lesson(store, "older-so-more-missed", age_days=10)
        _seed_query_history(_queries_spread(60, now - timedelta(days=10), now))

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        order = [f.metadata.get("lesson_id") for f in report.findings]
        assert order.index("older-so-more-missed") < order.index("newer-so-fewer-missed")

    @pytest.mark.asyncio
    async def test_unreachable_outrank_merely_stale(
        self, store, engine, no_query_history
    ):
        """A lesson nobody can reach beats one that only needs rewording."""
        created = datetime.now(UTC) - timedelta(days=40)
        await self._lesson(store, "cannot-be-reached", age_days=40)
        popular = Lesson(
            id="reached-often-but-old", trigger="popular", action="Act",
            tags=["test"], usage_count=500,
            last_refined=datetime.now(UTC) - timedelta(days=300),
        )
        await store.add_lesson(popular)
        _seed_query_history(_queries_spread(40, created, datetime.now(UTC)))

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        order = [f.metadata.get("lesson_id") for f in report.findings]
        assert order.index("cannot-be-reached") < order.index("reached-often-but-old")

    @pytest.mark.asyncio
    async def test_a_matched_lesson_is_never_called_unreachable(
        self, store, engine, no_query_history
    ):
        """usage_count counts a MATCH, which is what a rewritten trigger fixes."""
        now = datetime.now(UTC)
        await self._lesson(store, "matched-once", age_days=90, usage=1)
        _seed_query_history(_queries_spread(60, now - timedelta(days=90), now))

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        titles = [f.title for f in report.findings
                  if f.metadata.get("lesson_id") == "matched-once"]
        assert not [t for t in titles if "never reaches" in t]


class TestFindingsSurviveTheRun:
    """A run keeps what it found, not only how many things it found.

    Before this, only `{"finding_count": N}` reached rem_state. A cycle that
    found 105 unused lessons recommended a fix for each and then discarded every
    one, and the next run found the same 105 and discarded them again.
    """

    @pytest.mark.asyncio
    async def test_findings_are_readable_after_the_run(self, store, engine):
        lesson = Lesson(
            id="kept-finding", trigger="t", action="a", tags=["test"],
            usage_count=0, created_at=datetime.now(UTC) - timedelta(days=60),
        )
        await store.add_lesson(lesson)

        report = await engine.run(session_number=5, operations=["staleness_scan"])
        assert report.findings

        stored = await store.get_rem_findings(PROJECT, "staleness_scan")
        assert len(stored) == len(report.findings)
        assert stored[0]["title"] == report.findings[0].title
        assert stored[0]["metadata"]["lesson_id"] == report.findings[0].metadata["lesson_id"]
        assert stored[0]["options"], "the recommended action has to survive too"

    @pytest.mark.asyncio
    async def test_a_second_run_replaces_rather_than_appends(self, store, engine):
        """The same unused lesson found twice is one problem, not two."""
        lesson = Lesson(
            id="still-unused", trigger="t", action="a", tags=["test"],
            usage_count=0, created_at=datetime.now(UTC) - timedelta(days=60),
        )
        await store.add_lesson(lesson)

        await engine.run(session_number=5, operations=["staleness_scan"])
        first = await store.get_rem_findings(PROJECT, "staleness_scan")
        await engine.run(session_number=9, operations=["staleness_scan"])
        second = await store.get_rem_findings(PROJECT, "staleness_scan")

        assert first and len(second) == len(first)

    @pytest.mark.asyncio
    async def test_a_run_that_finds_nothing_clears_the_old_rows(self, store, engine):
        """Otherwise a fixed corpus keeps showing the problems it fixed."""
        lesson = Lesson(
            id="to-be-fixed", trigger="t", action="a", tags=["test"],
            usage_count=0, created_at=datetime.now(UTC) - timedelta(days=60),
        )
        await store.add_lesson(lesson)
        await engine.run(session_number=5, operations=["staleness_scan"])
        assert await store.get_rem_findings(PROJECT, "staleness_scan")

        await store.delete_lesson("to-be-fixed")
        await engine.run(session_number=9, operations=["staleness_scan"])
        assert await store.get_rem_findings(PROJECT, "staleness_scan") == []

    @pytest.mark.asyncio
    async def test_one_operation_does_not_clear_another(self, store, engine):
        await store.replace_rem_findings(
            PROJECT, "link_suggestions", 1,
            [RemFinding(operation="link_suggestions", title="kept", description="d")],
        )
        lesson = Lesson(
            id="unused-here", trigger="t", action="a", tags=["test"],
            usage_count=0, created_at=datetime.now(UTC) - timedelta(days=60),
        )
        await store.add_lesson(lesson)
        await engine.run(session_number=5, operations=["staleness_scan"])

        assert len(await store.get_rem_findings(PROJECT, "link_suggestions")) == 1


class TestLinkSuggestions:
    """An unlinked lesson is unreachable by BOTH retrieval paths.

    Search can miss it on wording, and the community bridge cannot reach it at
    all, because Louvain puts an isolated node in no community. On a live corpus
    38 of the 44 never-matched lessons carried no edges.
    """

    class _Store:
        """A vector store that answers with a fixed ranking."""

        def __init__(self, hits):
            self.hits = hits
            self.queries = []

        def search(self, query, limit=5, min_score=0.0, tags=None):
            self.queries.append(query)
            return self.hits

    # Distinct words per lesson. Giving every orphan the same trigger made them
    # collide with each other, and the duplicate filter below then removed
    # every proposal, so the test measured the filter instead of the cap.
    WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
             "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
             "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
             "victor", "whiskey", "xray", "yankee", "zulu", "zero", "one",
             "two", "three", "four", "five", "six", "seven", "eight", "nine")

    async def _orphan(self, store, lesson_id, index=None):
        word = self.WORDS[index % len(self.WORDS)] if index is not None else lesson_id
        await store.add_lesson(Lesson(
            id=lesson_id, trigger=f"{word} {lesson_id}", action="Act",
            tags=["test"],
        ))

    @pytest.mark.asyncio
    async def test_an_isolated_lesson_gets_a_proposal(self, store):
        await self._orphan(store, "alone")
        await self._orphan(store, "neighbour")
        vectors = self._Store([("neighbour", 0.81), ("alone", 0.99)])
        engine = RemEngine(store, project_id=PROJECT, vector_store=vectors)

        findings = await engine._link_suggestions()
        mine = [f for f in findings if f.metadata.get("lesson_id") == "alone"]
        assert len(mine) == 1
        links = mine[0].metadata["proposed_links"]
        assert links[0]["target_id"] == "neighbour"
        assert links[0]["relationship_type"] == "related"
        assert "alone" not in [link["target_id"] for link in links], \
            "a lesson must never be proposed as its own neighbour"

    @pytest.mark.asyncio
    async def test_a_lesson_that_already_has_a_link_is_left_alone(self, store):
        from mgcp.models import Relationship

        await self._orphan(store, "neighbour")
        await store.add_lesson(Lesson(
            id="connected", trigger="t", action="a", tags=["test"],
            relationships=[Relationship(target="neighbour", type="related")],
        ))
        vectors = self._Store([("neighbour", 0.81)])
        engine = RemEngine(store, project_id=PROJECT, vector_store=vectors)

        findings = await engine._link_suggestions()
        assert not [f for f in findings
                    if f.metadata.get("lesson_id") == "connected"]

    @pytest.mark.asyncio
    async def test_a_parent_is_not_an_orphan(self, store):
        """It has an edge, so the bridge can already reach it."""
        await store.add_lesson(Lesson(
            id="the-parent", trigger="t", action="a", tags=["test"]))
        await store.add_lesson(Lesson(
            id="the-child", trigger="t", action="a", tags=["test"],
            parent_id="the-parent"))
        vectors = self._Store([("whatever", 0.9)])
        engine = RemEngine(store, project_id=PROJECT, vector_store=vectors)

        flagged = {f.metadata.get("lesson_id")
                   for f in await engine._link_suggestions()}
        assert "the-parent" not in flagged
        assert "the-child" not in flagged

    @pytest.mark.asyncio
    async def test_a_trigger_twin_is_not_proposed_as_a_link(self, store):
        """Linking a duplicate pair papers over the duplication.

        This operation did exactly that on its first run: it proposed a link
        between two lessons that carried the same rule. A pair whose triggers
        collide belongs to duplicate_detection, which reports it for merging.
        """
        await self._orphan(store, "twin-a")
        await store.add_lesson(Lesson(
            id="twin-b", trigger="twin-a twin-b", action="Act", tags=["test"]))
        await store.add_lesson(Lesson(
            id="unrelated", trigger="kubernetes helm rollout", action="Act",
            tags=["test"]))
        vectors = self._Store([("twin-b", 0.95), ("unrelated", 0.70)])
        engine = RemEngine(store, project_id=PROJECT, vector_store=vectors)

        findings = await engine._link_suggestions()
        mine = [f for f in findings if f.metadata.get("lesson_id") == "twin-a"]
        assert len(mine) == 1
        targets = [link["target_id"] for link in mine[0].metadata["proposed_links"]]
        assert "twin-b" not in targets, \
            "the highest scoring neighbour was a duplicate and was proposed anyway"
        assert "unrelated" in targets

    @pytest.mark.asyncio
    async def test_no_vector_store_means_no_findings_not_a_crash(self, store):
        await self._orphan(store, "alone")
        engine = RemEngine(store, project_id=PROJECT)
        assert await engine._link_suggestions() == []

    @pytest.mark.asyncio
    async def test_a_weak_match_is_not_proposed(self, store):
        """The floor is the bridge's own, so one number governs both."""
        await self._orphan(store, "alone")
        vectors = self._Store([])     # nothing cleared min_score
        engine = RemEngine(store, project_id=PROJECT, vector_store=vectors)
        assert await engine._link_suggestions() == []

    @pytest.mark.asyncio
    async def test_the_batch_is_capped_and_says_how_many_are_left(self, store):
        from mgcp.rem_cycle import MAX_LINK_FINDINGS

        for i in range(MAX_LINK_FINDINGS + 4):
            await self._orphan(store, f"alone-{i}", index=i)
        vectors = self._Store([("alone-0", 0.7), ("alone-1", 0.7)])
        engine = RemEngine(store, project_id=PROJECT, vector_store=vectors)

        findings = await engine._link_suggestions()
        overflow = [f for f in findings if "more unlinked" in f.title]
        assert len(overflow) == 1
        assert overflow[0].metadata["unlinked_total"] == MAX_LINK_FINDINGS + 4
        assert len(findings) == MAX_LINK_FINDINGS + 1


class TestEveryScheduledOperationHasAHandler:
    """A scheduled name with no handler raises instead of reporting health.

    This is the wiring test. 48 workflow references to lessons were once
    validated by nothing, so one pointing at a lesson that shipped nowhere
    looked right in the source and arrived empty on every fresh install.
    """

    @pytest.mark.asyncio
    async def test_all_shipped_operations_dispatch(self, store):
        from mgcp.rem_config import DEFAULT_SCHEDULES

        engine = RemEngine(store, project_id=PROJECT)
        for operation in DEFAULT_SCHEDULES:
            # A missing handler raises ValueError before anything is written.
            await engine._run_operation(operation, session_number=1)

    @pytest.mark.asyncio
    async def test_an_unknown_operation_still_raises(self, store):
        engine = RemEngine(store, project_id=PROJECT)
        with pytest.raises(ValueError, match="unknown REM operation"):
            await engine._run_operation("no-such-operation", session_number=1)


class TestTheReportIsReadable:
    """A report nobody reads enforces nothing.

    Every finding used to print in full. One run produced 109, each with a
    title, a description and three options, which is over 600 lines in a single
    tool response. It was skimmed, and the recommended fix for all 109 was lost.
    """

    def _findings(self, operation, count):
        return [
            RemFinding(
                operation=operation,
                title=f"{operation} finding {i}",
                description="d",
                options=[{"label": "Fix", "description": "do it"}],
            )
            for i in range(count)
        ]

    def test_each_operation_is_capped(self):
        from mgcp.server import FINDINGS_SHOWN_PER_OPERATION, _render_findings

        text = "\n".join(_render_findings(self._findings("staleness_scan", 40)))
        shown = text.count("staleness_scan finding")
        assert shown == FINDINGS_SHOWN_PER_OPERATION
        assert "35 more from staleness_scan" in text

    def test_the_total_is_still_stated(self):
        """Capping what is PRINTED must not understate what was found."""
        from mgcp.server import _render_findings

        text = "\n".join(_render_findings(self._findings("staleness_scan", 40)))
        assert "40 Finding(s)" in text

    def test_one_noisy_operation_cannot_crowd_out_another(self):
        """This is why the cap is per operation and not overall."""
        from mgcp.server import _render_findings

        findings = (self._findings("staleness_scan", 100)
                    + self._findings("link_suggestions", 2))
        text = "\n".join(_render_findings(findings))
        assert "link_suggestions finding 0" in text
        assert "link_suggestions finding 1" in text

    def test_a_short_list_says_nothing_about_more(self):
        from mgcp.server import _render_findings

        text = "\n".join(_render_findings(self._findings("staleness_scan", 2)))
        assert "more from" not in text
        assert text.count("staleness_scan finding") == 2

    def test_the_recommended_option_is_marked(self):
        from mgcp.server import _render_findings

        text = "\n".join(_render_findings(self._findings("staleness_scan", 1)))
        assert "(Recommended)" in text
