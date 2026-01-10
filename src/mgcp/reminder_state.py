"""Self-directed reminder system for LLM workflow continuity.

This module manages scheduled reminders that the LLM sets for itself.
Use case: At step N, schedule knowledge needed for step N+1.

State file location: ~/.mgcp/reminder_state.json

How it works:
1. LLM calls schedule_reminder(after_calls=2, message="...", lesson_ids="...", workflow_step="...")
2. Hook increments counter on each UserPromptSubmit
3. When counter reaches threshold, hook injects the reminder content
4. Pattern-based workflow hooks run INDEPENDENTLY (both can fire)
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

# State file location
STATE_DIR = Path.home() / ".mgcp"
STATE_FILE = STATE_DIR / "reminder_state.json"


def load_state() -> dict:
    """Load current state from file, or return defaults."""
    defaults = {
        "current_call_count": 0,
        "last_updated": datetime.now(UTC).isoformat(),
        # Reminder scheduling
        "remind_at_call": 0,  # Fire reminder when call count reaches this
        "remind_at_time": 0,  # Fire reminder at this timestamp (alternative to call-based)
        # Reminder content
        "task_note": "",  # What task is being worked on
        "reminder_message": "",  # Custom message to inject
        "lesson_ids": [],  # Lesson IDs to surface
        "workflow_step": "",  # Workflow/step to load (e.g., "bug-fix/investigate")
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
    except (IOError, OSError):
        pass


def schedule_reminder(
    after_calls: int | None = None,
    after_minutes: int | None = None,
    note: str = "",
    message: str = "",
    lesson_ids: list[str] | None = None,
    workflow_step: str = "",
) -> dict:
    """Schedule a self-directed reminder for a future checkpoint.

    Use this at the end of a workflow step to set up knowledge for the next step.
    The reminder fires regardless of what the user types - it's counter/timer based.

    Args:
        after_calls: Fire reminder after N user messages
        after_minutes: Fire reminder after N minutes
        note: What task is being worked on (context)
        message: Custom message to inject when reminder fires
        lesson_ids: Lesson IDs to surface when reminder fires
        workflow_step: Workflow/step to load (e.g., "feature-development/plan")

    Returns:
        Updated state dict
    """
    state = load_state()

    if after_calls is not None:
        state["remind_at_call"] = state["current_call_count"] + after_calls

    if after_minutes is not None:
        state["remind_at_time"] = time.time() + (after_minutes * 60)

    if note:
        state["task_note"] = note
    if message:
        state["reminder_message"] = message
    if lesson_ids is not None:
        state["lesson_ids"] = lesson_ids
    if workflow_step:
        state["workflow_step"] = workflow_step

    save_state(state)
    return state


def increment_counter() -> int:
    """Increment the call counter and return new value."""
    state = load_state()
    state["current_call_count"] = state.get("current_call_count", 0) + 1
    save_state(state)
    return state["current_call_count"]


def check_and_consume_reminder() -> dict | None:
    """Check if a scheduled reminder should fire, and consume it if so.

    Call this from the hook on each UserPromptSubmit.

    Returns:
        dict with reminder data if ready to fire, None otherwise.
        Keys: message, lesson_ids, workflow_step, task_note
    """
    state = load_state()
    current_call = state.get("current_call_count", 0)
    now = time.time()

    # Check if reminder threshold reached
    remind_at_call = state.get("remind_at_call", 0)
    remind_at_time = state.get("remind_at_time", 0)

    call_ready = remind_at_call > 0 and current_call >= remind_at_call
    time_ready = remind_at_time > 0 and now >= remind_at_time

    if not call_ready and not time_ready:
        return None

    # Check if there's any reminder content
    message = state.get("reminder_message", "")
    lesson_ids = state.get("lesson_ids", [])
    workflow_step = state.get("workflow_step", "")

    if not message and not lesson_ids and not workflow_step:
        return None

    # Build reminder data
    reminder = {
        "message": message,
        "lesson_ids": lesson_ids,
        "workflow_step": workflow_step,
        "task_note": state.get("task_note", ""),
    }

    # Consume the reminder (clear it so it only fires once)
    state["remind_at_call"] = 0
    state["remind_at_time"] = 0
    state["reminder_message"] = ""
    state["lesson_ids"] = []
    state["workflow_step"] = ""
    state["task_note"] = ""
    save_state(state)

    return reminder


def get_status() -> dict:
    """Get current reminder state status for display."""
    state = load_state()
    current_call = state.get("current_call_count", 0)
    now = time.time()

    remind_at_call = state.get("remind_at_call", 0)
    remind_at_time = state.get("remind_at_time", 0)

    return {
        "current_call_count": current_call,
        "last_updated": state.get("last_updated", ""),
        "scheduled_reminder": {
            "at_call": remind_at_call,
            "at_time": remind_at_time,
            "calls_until": max(0, remind_at_call - current_call) if remind_at_call else 0,
            "minutes_until": max(0, int((remind_at_time - now) / 60)) if remind_at_time else 0,
            "has_content": bool(state.get("reminder_message") or state.get("lesson_ids") or state.get("workflow_step")),
            "message": state.get("reminder_message", ""),
            "lesson_ids": state.get("lesson_ids", []),
            "workflow_step": state.get("workflow_step", ""),
            "task_note": state.get("task_note", ""),
        },
    }


def reset_state() -> dict:
    """Reset state to defaults."""
    state = {
        "current_call_count": 0,
        "last_updated": datetime.now(UTC).isoformat(),
        "remind_at_call": 0,
        "remind_at_time": 0,
        "task_note": "",
        "reminder_message": "",
        "lesson_ids": [],
        "workflow_step": "",
    }
    save_state(state)
    return state