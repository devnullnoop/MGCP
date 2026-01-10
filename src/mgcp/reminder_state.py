"""Reminder state management for LLM-controlled suppression.

This module manages the state file that coordinates between:
- MCP tool (set_reminder_boundary) - writes suppression boundaries
- Hooks (task-start-reminder.py) - reads state to decide whether to fire

State file location: ~/.mgcp/reminder_state.json

A/B Testing:
- Set MGCP_REMINDER_MODE=counter for call-count based suppression
- Set MGCP_REMINDER_MODE=timer for time-based suppression
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

# State file location
STATE_DIR = Path.home() / ".mgcp"
STATE_FILE = STATE_DIR / "reminder_state.json"

# A/B mode selection
ReminderMode = Literal["counter", "timer"]


def get_mode() -> ReminderMode:
    """Get the current reminder mode from environment or default."""
    mode = os.environ.get("MGCP_REMINDER_MODE", "counter").lower()
    if mode not in ("counter", "timer"):
        return "counter"
    return mode


def load_state() -> dict:
    """Load current state from file, or return defaults."""
    defaults = {
        "mode": get_mode(),
        "suppress_until_call": 0,
        "suppress_until_time": 0,
        "current_call_count": 0,
        "task_note": "",
        "last_updated": datetime.now(UTC).isoformat(),
    }

    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                state = json.load(f)
                # Merge with defaults for any missing keys
                for key, value in defaults.items():
                    if key not in state:
                        state[key] = value
                return state
    except (json.JSONDecodeError, IOError, OSError):
        pass

    return defaults


def save_state(state: dict) -> None:
    """Save state to file, creating directory if needed."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state["last_updated"] = datetime.now(UTC).isoformat()
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except (IOError, OSError) as e:
        # Log but don't crash - graceful degradation
        pass


def set_boundary(
    suppress_for_calls: int | None = None,
    suppress_for_minutes: int | None = None,
    note: str = "",
) -> dict:
    """Set a reminder suppression boundary.

    Args:
        suppress_for_calls: Suppress reminders for N hook checks (counter mode)
        suppress_for_minutes: Suppress reminders for N minutes (timer mode)
        note: Optional note about what task is being worked on

    Returns:
        Updated state dict with confirmation of what was set
    """
    state = load_state()
    mode = get_mode()
    state["mode"] = mode

    if suppress_for_calls is not None:
        state["suppress_until_call"] = state["current_call_count"] + suppress_for_calls
        state["mode"] = "counter"  # Override mode if explicitly setting calls

    if suppress_for_minutes is not None:
        state["suppress_until_time"] = time.time() + (suppress_for_minutes * 60)
        state["mode"] = "timer"  # Override mode if explicitly setting time

    if note:
        state["task_note"] = note

    save_state(state)
    return state


def should_suppress() -> tuple[bool, str]:
    """Check if reminders should be suppressed.

    Also increments the call counter for counter mode tracking.

    Returns:
        Tuple of (should_suppress: bool, reason: str)
    """
    state = load_state()
    mode = state.get("mode", "counter")

    # Increment call counter
    state["current_call_count"] = state.get("current_call_count", 0) + 1
    save_state(state)

    if mode == "counter":
        suppress_until = state.get("suppress_until_call", 0)
        current = state["current_call_count"]
        if current < suppress_until:
            remaining = suppress_until - current
            return True, f"Suppressed ({remaining} calls remaining)"
        return False, "Counter expired"

    elif mode == "timer":
        suppress_until = state.get("suppress_until_time", 0)
        now = time.time()
        if now < suppress_until:
            remaining_mins = int((suppress_until - now) / 60)
            return True, f"Suppressed ({remaining_mins} minutes remaining)"
        return False, "Timer expired"

    return False, "Unknown mode"


def get_status() -> dict:
    """Get current reminder state status for display."""
    state = load_state()
    mode = state.get("mode", "counter")

    status = {
        "mode": mode,
        "current_call_count": state.get("current_call_count", 0),
        "task_note": state.get("task_note", ""),
        "last_updated": state.get("last_updated", ""),
    }

    if mode == "counter":
        suppress_until = state.get("suppress_until_call", 0)
        current = state.get("current_call_count", 0)
        status["suppress_until_call"] = suppress_until
        status["calls_remaining"] = max(0, suppress_until - current)
        status["is_suppressed"] = current < suppress_until
    else:
        suppress_until = state.get("suppress_until_time", 0)
        now = time.time()
        status["suppress_until_time"] = suppress_until
        status["minutes_remaining"] = max(0, int((suppress_until - now) / 60))
        status["is_suppressed"] = now < suppress_until

    return status


def reset_state() -> dict:
    """Reset state to defaults. Useful for testing or clearing stuck state."""
    state = {
        "mode": get_mode(),
        "suppress_until_call": 0,
        "suppress_until_time": 0,
        "current_call_count": 0,
        "task_note": "",
        "last_updated": datetime.now(UTC).isoformat(),
    }
    save_state(state)
    return state