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

    def test_next_due_fibonacci(self):
        s = OperationSchedule(strategy="fibonacci")
        assert next_due_session(s, last_run_session=5) == 8
        assert next_due_session(s, last_run_session=8) == 13
        assert next_due_session(s, last_run_session=0) == 5


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
            "knowledge_extraction", "context_summary", "intent_calibration",
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
    @pytest.mark.parametrize("last_run", [0, 1, 5, 10, 37, 98, 144])
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
