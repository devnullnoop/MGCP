#!/usr/bin/env python3
"""UserPromptSubmit dispatcher for MGCP v2.0.

Intent classification is handled by the LLM via the routing prompt injected at
SessionStart. This hook only handles:

1. Scheduled reminders (counter/timer-based, fires regardless of message content)
2. Workflow state injection (surfaces active workflow context)

No regex pattern matching. Zero intent detection. ~60 lines.
"""
import json
import os
import sys
import time
from pathlib import Path

STATE_FILE = Path(os.environ.get("MGCP_STATE_FILE", str(Path.home() / ".mgcp" / "workflow_state.json")))


def _load_state() -> dict:
    """Load workflow state from file."""
    defaults = {
        "current_call_count": 0,
        "remind_at_call": 0,
        "remind_at_time": 0,
        "reminder_message": "",
        "lesson_ids": [],
        "workflow_step": "",
        "task_note": "",
        "active_workflow": None,
        "current_step": None,
        "workflow_complete": False,
        "steps_completed": [],
    }
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                state = json.load(f)
                for key, value in defaults.items():
                    if key not in state:
                        state[key] = value
                return state
    except (json.JSONDecodeError, IOError, OSError):
        pass
    return defaults


def _save_state(state: dict) -> None:
    """Save state to file."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except (IOError, OSError):
        pass


def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    output_parts = []
    state = _load_state()

    # Increment call counter
    state["current_call_count"] = state.get("current_call_count", 0) + 1
    _save_state(state)

    # 1. Check scheduled reminders
    current_call = state["current_call_count"]
    now = time.time()
    remind_at_call = state.get("remind_at_call", 0)
    remind_at_time = state.get("remind_at_time", 0)

    call_ready = remind_at_call > 0 and current_call >= remind_at_call
    time_ready = remind_at_time > 0 and now >= remind_at_time

    if call_ready or time_ready:
        message = state.get("reminder_message", "")
        lesson_ids = state.get("lesson_ids", [])
        workflow_step = state.get("workflow_step", "")

        if message or lesson_ids or workflow_step:
            lines = ["<scheduled-reminder>", "SCHEDULED REMINDER (self-directed)", ""]
            if message:
                lines.extend([f"**Message:** {message}", ""])
            if workflow_step:
                if "/" in workflow_step:
                    wf_id, step_id = workflow_step.split("/", 1)
                    lines.extend([f'**Next Step:** Call get_workflow_step("{wf_id}", "{step_id}", expand_lessons=true)', ""])
                else:
                    lines.extend([f'**Workflow:** Call get_workflow("{workflow_step}")', ""])
            if lesson_ids:
                if isinstance(lesson_ids, str):
                    lesson_ids = [lid.strip() for lid in lesson_ids.split(",") if lid.strip()]
                lines.extend(["**Lessons:** " + ", ".join(lesson_ids), ""])
            lines.append("</scheduled-reminder>")
            output_parts.append("\n".join(lines))

            # Consume the reminder
            state["remind_at_call"] = 0
            state["remind_at_time"] = 0
            state["reminder_message"] = ""
            state["lesson_ids"] = []
            state["workflow_step"] = ""
            state["task_note"] = ""
            _save_state(state)

    # 2. Inject workflow state if active
    active_workflow = state.get("active_workflow")
    if active_workflow and not state.get("workflow_complete", False):
        current_step = state.get("current_step", "unknown")
        completed = state.get("steps_completed", [])
        completed_str = ", ".join(completed) if completed else "none"
        output_parts.append(
            f"<workflow-state>\n"
            f"ACTIVE: {active_workflow} | STEP: {current_step} | DONE: {completed_str}\n"
            f"</workflow-state>"
        )

    if output_parts:
        print("\n\n".join(output_parts))

    sys.exit(0)


if __name__ == "__main__":
    main()
