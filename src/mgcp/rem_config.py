"""REM cycle scheduling strategies and configuration.

Supports linear, fibonacci, and logarithmic schedules for different
REM operations. Each operation can run on its own cadence.
"""

import math
from dataclasses import dataclass
from typing import Literal

StrategyType = Literal["linear", "fibonacci", "logarithmic"]


@dataclass
class OperationSchedule:
    """Schedule configuration for a single REM operation."""

    strategy: StrategyType = "linear"
    interval: int = 10  # For linear: every N sessions
    base_interval: int = 10  # For logarithmic: first run at session N
    scale: float = 2.0  # For logarithmic: ln(session / base) * scale


# Pre-computed fibonacci numbers up to session ~1000
_FIBONACCI = [5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987]


def is_due(schedule: OperationSchedule, current_session: int, last_run_session: int) -> bool:
    """Check if an operation is due at the current session number."""
    if current_session <= last_run_session:
        return False

    if schedule.strategy == "linear":
        return _is_due_linear(schedule.interval, current_session, last_run_session)
    elif schedule.strategy == "fibonacci":
        return _is_due_fibonacci(current_session, last_run_session)
    elif schedule.strategy == "logarithmic":
        return _is_due_logarithmic(schedule, current_session, last_run_session)
    return False


MAX_LOOKAHEAD = 1000


def next_due_session(
    schedule: OperationSchedule,
    last_run_session: int,
    lookahead: int = MAX_LOOKAHEAD,
) -> int | None:
    """The first session after ``last_run_session`` at which `is_due` fires.

    Derived by asking `is_due` rather than by re-deriving the arithmetic,
    because the two answers are one fact and a second implementation of a
    schedule is a second opinion about it. The linear branch used to compute
    the next multiple of the interval — a grid — while `is_due` measures
    sessions elapsed since the last run. For `staleness_scan` (interval 5)
    last run at 98, the grid said 100 and `is_due` did not fire until 103, so
    `rem_status` published a due date three sessions early and SessionStart
    warned "REM Operations Overdue" for operations that were not.

    The parameter is `last_run_session`, not `current_session`: both callers
    always passed the last run, and the old name is what made a grid look
    like a reasonable reading.

    Returns None when the schedule does not fire within ``lookahead``
    sessions (e.g. a non-positive interval, which is never due). Callers and
    the `next_due_session` column already treat that as unknown.
    """
    for session in range(last_run_session + 1, last_run_session + lookahead + 1):
        if is_due(schedule, session, last_run_session):
            return session
    return None


def _is_due_linear(interval: int, current: int, last_run: int) -> bool:
    """Linear: due every N sessions."""
    if interval <= 0:
        return False
    sessions_since = current - last_run
    return sessions_since >= interval


def _is_due_fibonacci(current: int, last_run: int) -> bool:
    """Fibonacci: due at fibonacci-numbered sessions (5, 8, 13, 21, ...)."""
    for fib in _FIBONACCI:
        if fib > last_run and fib <= current:
            return True
    # Extend if needed
    if current > _FIBONACCI[-1]:
        a, b = _FIBONACCI[-2], _FIBONACCI[-1]
        while b <= current:
            a, b = b, a + b
            if b > last_run and b <= current:
                return True
    return False


def _is_due_logarithmic(schedule: OperationSchedule, current: int, last_run: int) -> bool:
    """Logarithmic: gap between runs grows as ln(session/base) * scale."""
    if current < schedule.base_interval:
        return False
    gap = max(1, int(math.log(max(1, current / schedule.base_interval)) * schedule.scale))
    sessions_since = current - last_run
    return sessions_since >= gap


# Default schedules for each REM operation
DEFAULT_SCHEDULES: dict[str, OperationSchedule] = {
    "staleness_scan": OperationSchedule(strategy="linear", interval=5),
    "duplicate_detection": OperationSchedule(strategy="linear", interval=10),
    "community_detection": OperationSchedule(strategy="fibonacci"),
    "knowledge_extraction": OperationSchedule(
        strategy="logarithmic", base_interval=10, scale=2.0
    ),
    "context_summary": OperationSchedule(strategy="linear", interval=20),
    "intent_calibration": OperationSchedule(strategy="linear", interval=10),
}
