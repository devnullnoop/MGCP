"""Tests for REM cycle scheduling strategies."""

import pytest

from mgcp.rem_config import (
    DEFAULT_SCHEDULES,
    OperationSchedule,
    is_due,
    next_due_session,
)


class TestLinearSchedule:
    """Test linear (every N sessions) scheduling."""

    def test_due_at_interval(self):
        s = OperationSchedule(strategy="linear", interval=5)
        assert is_due(s, current_session=5, last_run_session=0) is True

    def test_not_due_before_interval(self):
        s = OperationSchedule(strategy="linear", interval=5)
        assert is_due(s, current_session=3, last_run_session=0) is False

    def test_due_after_multiple_intervals(self):
        s = OperationSchedule(strategy="linear", interval=5)
        assert is_due(s, current_session=15, last_run_session=5) is True

    def test_not_due_if_just_ran(self):
        s = OperationSchedule(strategy="linear", interval=5)
        assert is_due(s, current_session=10, last_run_session=10) is False

    def test_next_due(self):
        """Next due is last run + interval, not the next multiple of it.

        These used to read 10 and 15 — the interval GRID — while is_due
        measures sessions elapsed since the last run. A run at 7 does not
        make the operation due at 10; it makes it due at 12.
        """
        s = OperationSchedule(strategy="linear", interval=5)
        assert next_due_session(s, last_run_session=7) == 12
        assert next_due_session(s, last_run_session=10) == 15

    def test_next_due_is_none_when_the_schedule_never_fires(self):
        s = OperationSchedule(strategy="linear", interval=0)
        assert next_due_session(s, last_run_session=5) is None


class TestFibonacciSchedule:
    """Test fibonacci scheduling."""

    def test_due_at_fibonacci_numbers(self):
        s = OperationSchedule(strategy="fibonacci")
        assert is_due(s, current_session=5, last_run_session=0) is True
        assert is_due(s, current_session=8, last_run_session=5) is True
        assert is_due(s, current_session=13, last_run_session=8) is True
        assert is_due(s, current_session=21, last_run_session=13) is True

    def test_not_due_between_fibonacci(self):
        s = OperationSchedule(strategy="fibonacci")
        assert is_due(s, current_session=6, last_run_session=5) is False
        assert is_due(s, current_session=7, last_run_session=5) is False

class TestLogarithmicSchedule:
    """Test logarithmic scheduling."""

    def test_not_due_before_base(self):
        s = OperationSchedule(strategy="logarithmic", base_interval=10, scale=2.0)
        assert is_due(s, current_session=5, last_run_session=0) is False

    def test_due_at_base(self):
        s = OperationSchedule(strategy="logarithmic", base_interval=10, scale=2.0)
        assert is_due(s, current_session=10, last_run_session=0) is True

    def test_gap_grows_over_time(self):
        """Later sessions should have larger gaps between runs."""
        s = OperationSchedule(strategy="logarithmic", base_interval=10, scale=5.0)
        # Early: small gap
        assert is_due(s, current_session=15, last_run_session=10) is True
        # Later: needs bigger gap
        # At session 100, gap = ln(100/10) * 5 = ln(10) * 5 ≈ 11.5
        assert is_due(s, current_session=105, last_run_session=100) is False
        assert is_due(s, current_session=112, last_run_session=100) is True


class TestDefaultSchedules:
    """Test default schedule configuration."""

    def test_all_operations_have_schedules(self):
        expected = {
            "staleness_scan", "duplicate_detection", "community_detection",
            "knowledge_extraction", "intent_calibration", "gate_audit_review",
        }
        assert set(DEFAULT_SCHEDULES.keys()) == expected

    def test_staleness_is_frequent(self):
        s = DEFAULT_SCHEDULES["staleness_scan"]
        assert s.strategy == "linear"
        assert s.interval == 5

    def test_community_uses_fibonacci(self):
        s = DEFAULT_SCHEDULES["community_detection"]
        assert s.strategy == "fibonacci"

    def test_extraction_uses_logarithmic(self):
        s = DEFAULT_SCHEDULES["knowledge_extraction"]
        assert s.strategy == "logarithmic"


class TestNextDueAgreesWithIsDue:
    """The published due date and the predicate that gates execution are one
    fact. They were two implementations of it, and they disagreed."""

    @pytest.mark.parametrize("schedule", list(DEFAULT_SCHEDULES.values()), ids=list(DEFAULT_SCHEDULES))
    @pytest.mark.parametrize("last_run", [0, 1, 5, 7, 8, 10, 37, 98, 144])
    def test_published_date_is_the_first_session_that_actually_fires(self, schedule, last_run):
        nxt = next_due_session(schedule, last_run)
        assert nxt is not None, "every shipped schedule must fire eventually"

        assert is_due(schedule, nxt, last_run), (
            f"published next due {nxt} is not actually due (last run {last_run})"
        )
        not_yet = [s for s in range(last_run + 1, nxt) if is_due(schedule, s, last_run)]
        assert not not_yet, (
            f"published next due {nxt} is late: {not_yet} fire earlier (last run {last_run})"
        )


def _engine(tmp_path, **kw):
    from mgcp.persistence import LessonStore
    from mgcp.rem_cycle import RemEngine

    store = LessonStore(str(tmp_path / "test.db"))
    return RemEngine(store=store, schedules=DEFAULT_SCHEDULES, project_id="p1", **kw), store


class TestOperationDispatch:
    """Findings an operation cannot produce, and operations that do not exist."""

    @pytest.mark.asyncio
    async def test_misspelled_operation_reports_failure_and_writes_no_state(self, tmp_path):
        """rem_run does not validate its comma-separated operations argument.

        `stalenes_scan` used to fall through to zero findings, print "No
        findings. Knowledge base looks healthy." and leave a rem_state row
        under the bogus name for repair_rem_state to sweep up later.
        """
        engine, store = _engine(tmp_path)

        report = await engine.run(session_number=5, operations=["stalenes_scan"])

        assert [f.title for f in report.findings] == ["stalenes_scan failed"]
        assert "unknown REM operation" in report.findings[0].description
        assert await store.get_rem_state("p1") == []

    @pytest.mark.asyncio
    async def test_unlinked_lessons_are_reported_as_orphans(self, tmp_path):
        """Louvain partitions every node, so an unlinked lesson comes back as a
        singleton community, not as a lesson missing from the partition. The
        orphan finding used to look for the latter and could never fire."""
        from mgcp.models import Lesson

        engine, store = _engine(tmp_path)
        for i in range(5):
            await store.add_lesson(
                Lesson(id=f"loner-{i}", trigger=f"t{i}", action="a", tags=["x"])
            )

        findings = await engine._community_detection()
        orphan = next(f for f in findings if "orphan" in f.title)
        assert orphan.metadata["orphan_ids"] == [f"loner-{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_pending_todos_are_found_in_a_project_with_no_notes(self, tmp_path):
        """Knowledge extraction used to skip a whole project when none of its
        snapshots carried a note, gated on a list of notes it never read — and
        the todo check below does not depend on notes."""
        import hashlib

        from mgcp.models import ProjectContext, ProjectTodo

        engine, store = _engine(tmp_path)
        path = "/test/no-notes"
        ctx = ProjectContext(
            project_id=hashlib.sha256(path.encode()).hexdigest()[:12],
            project_name="No Notes",
            project_path=path,
            session_count=10,
            notes="",
            todos=[ProjectTodo(content=f"Task {i}", status="pending") for i in range(3)],
        )
        for session in (10, 11, 12):
            ctx.session_count = session
            await store.save_project_context(ctx)

        findings = await engine._knowledge_extraction()
        assert [f.metadata["pending_count"] for f in findings] == [3]
