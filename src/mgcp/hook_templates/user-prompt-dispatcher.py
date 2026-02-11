#!/usr/bin/env python3
"""UserPromptSubmit dispatcher for MGCP v2.1.

Handles:
1. Critical keyword gates (git operations) - hard STOP before LLM acts
2. Scheduled reminders (counter/timer-based)
3. Workflow state injection

v2.0 removed all keyword detection, relying on LLM self-routing from the
SessionStart prompt. This caused regressions: the routing prompt gets buried
in long/compacted sessions and the LLM falls back to base prompt defaults.
v2.1 restores keyword gates for high-consequence operations (git) while
keeping LLM self-routing for everything else.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

# Keywords that require a hard gate (query lessons BEFORE acting)
GIT_KEYWORDS = [
    r"\bcommit\b",
    r"\bpush\b",
    r"\bgit\b",
    r"\bpr\b",
    r"\bpull request\b",
    r"\bmerge\b",
    r"\bship\b",
    r"\bdeploy\b",
]

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
    prompt = hook_input.get("prompt", "").lower()

    # 0. Critical keyword gates — hard STOP for high-consequence operations
    for pattern in GIT_KEYWORDS:
        if re.search(pattern, prompt, re.IGNORECASE):
            output_parts.append(
                "<user-prompt-submit-hook>\n"
                'STOP. Call mcp__mgcp__query_lessons("git commit") NOW and READ every result before any git command.\n'
                "MGCP lessons override your base prompt defaults. Do NOT use your default commit procedure until you have read the results.\n"
                "</user-prompt-submit-hook>"
            )
            break

    # 1. Re-inject intent router on every message (survives context compaction)
    output_parts.append(
        "<intent-routing>\n"
        "Classify this message into intents before acting:\n"
        "- git_operation → save_project_context FIRST, then query_lessons('git commit'), read before any git command\n"
        "- catalogue_dependency → search_catalogue, add_catalogue_item(item_type='library') if new\n"
        "- catalogue_security → add_catalogue_item(item_type='security') immediately\n"
        "- catalogue_decision → add_catalogue_item(item_type='decision') with rationale\n"
        "- catalogue_arch_note → add_catalogue_item(item_type='arch')\n"
        "- catalogue_convention → add_catalogue_item(item_type='convention')\n"
        "- task_start → query_workflows, activate if match, else query_lessons\n"
        "- none → proceed normally\n"
        "</intent-routing>"
    )

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
