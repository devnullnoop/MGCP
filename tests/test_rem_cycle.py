"""Tests for REM cycle engine."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from mgcp.models import Lesson, ProjectContext, ProjectTodo
from mgcp.persistence import LessonStore
from mgcp.rem_config import OperationSchedule
from mgcp.rem_cycle import RemEngine, RemFinding


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        yield db_path


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
    async def test_finds_unused_old_lessons(self, store, engine):
        """Lessons created 30+ days ago with 0 retrievals should be flagged."""
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
        assert "never been retrieved" in stale[0].description

    @pytest.mark.asyncio
    async def test_ignores_recently_created(self, store, engine):
        """Lessons created recently should not be flagged even with 0 usage."""
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
    async def test_finds_heavily_used_but_stale(self, store, engine):
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
        assert "hasn't been refined" in stale[0].description


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
