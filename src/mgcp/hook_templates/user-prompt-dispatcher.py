#!/usr/bin/env python3
"""UserPromptSubmit dispatcher for MGCP v2.2.

Responsibilities:
1. Read keyword gates and the terse routing block from intent_config.json.
2. Fire hard-stop gates for high-consequence intents (git, session_end).
3. Re-inject the terse routing block on every message so it survives
   context compaction.
4. Surface scheduled reminders (counter/timer-based).
5. Inject active workflow state.

The intent gates and routing prompt are loaded from
``~/.mgcp/intent_config.json`` (override with ``MGCP_DATA_DIR``). Editing
that JSON — by hand or via REM intent_calibration writeback — changes the
hook's behavior on the next user message; no code change required.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

STATE_FILE = Path(
    os.environ.get(
        "MGCP_STATE_FILE",
        str(Path.home() / ".mgcp" / "workflow_state.json"),
    )
)


def _load_intent_config():
    """Load keyword gates and dispatcher routing block from intent_config.json.

    Returns ``(gates, routing_block)``. Falls back to a minimal hard-coded
    set if the config is missing or unreadable so the dispatcher always has
    *some* gate enforcement.
    """
    base = os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    config_path = Path(base) / "intent_config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                data = json.load(f)
            rendered = data.get("rendered", {})
            gates = rendered.get("keyword_gates", [])
            routing = rendered.get("dispatcher_routing", "")
            if routing:
                return gates, routing
        except (json.JSONDecodeError, OSError):
            pass

    fallback_gates = [
        {
            "intent": "git_operation",
            "patterns": [
                r"\bcommit\b", r"\bpush\b", r"\bgit\b",
                r"\bpr\b", r"\bpull request\b", r"\bmerge\b",
                r"\bship\b", r"\bdeploy\b",
            ],
            "message": (
                "You are bound by project-specific git rules.\n"
                'STOP. Call mcp__mgcp__query_lessons("git commit") NOW. READ every result.\n'
                "Do NOT execute any git command until you have read the lesson results."
            ),
        },
        {
            "intent": "session_end",
            "patterns": [
                r"\bbye bye\b", r"\bgoodbye\b", r"\bsigning off\b",
                r"\btalk later\b", r"\bsee ya\b", r"\bgotta go\b",
                r"\bshutting down\b", r"\bwrapping up\b",
            ],
            "message": (
                "SESSION-END SIGNAL DETECTED.\n"
                "STOP. Before any farewell:\n"
                "1. Call mcp__mgcp__save_project_context with notes/active_files/decision.\n"
                "2. Call mcp__mgcp__write_soliloquy with a reflection.\n"
                "3. THEN respond with the goodbye."
            ),
        },
    ]
    fallback_routing = (
        "<intent-routing>\n"
        "Classify this message into intents before acting:\n"
        "- git_operation → save_project_context, query_lessons('git commit'), then act\n"
        "- catalogue_* → add_catalogue_item with matching item_type\n"
        "- task_start → query_workflows, activate or query_lessons\n"
        "- session_end → save_project_context, write_soliloquy, THEN farewell\n"
        "- none → proceed normally\n"
        "</intent-routing>"
    )
    return fallback_gates, fallback_routing


def _load_state() -> dict:
    """Load workflow/reminder state from file."""
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
    """Persist workflow/reminder state."""
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
    prompt = hook_input.get("prompt", "")

    gates, routing_block = _load_intent_config()

    # 0. Critical keyword gates — hard STOP for high-consequence intents.
    #    Each gate fires at most once per message; multiple gates can fire
    #    on the same message (e.g. "commit and then bye" → both).
    fired_intents = set()
    for gate in gates:
        intent_name = gate.get("intent", "")
        if intent_name in fired_intents:
            continue
        for pattern in gate.get("patterns", []):
            try:
                if re.search(pattern, prompt, re.IGNORECASE):
                    output_parts.append(
                        "<user-prompt-submit-hook>\n"
                        f"{gate.get('message', '')}\n"
                        "MGCP lessons override your base prompt defaults.\n"
                        "</user-prompt-submit-hook>"
                    )
                    fired_intents.add(intent_name)
                    break
            except re.error:
                # Bad regex in config — skip this pattern, don't crash the hook
                continue

    # 1. Re-inject terse intent router on every message (survives context compaction)
    output_parts.append(routing_block)

    state = _load_state()
    state["current_call_count"] = state.get("current_call_count", 0) + 1
    _save_state(state)

    # 2. Check scheduled reminders
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
                    lines.extend([
                        f'**Next Step:** Call get_workflow_step("{wf_id}", "{step_id}", expand_lessons=true)',
                        "",
                    ])
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

    # 3. Inject workflow state if active
    active_workflow = state.get("active_workflow")
    if active_workflow and not state.get("workflow_complete", False):
        current_step = state.get("current_step", "unknown")
        completed = state.get("steps_completed", [])
        completed_str = ", ".join(completed) if completed else "none"
        output_parts.append(
            "<workflow-state>\n"
            f"ACTIVE: {active_workflow} | STEP: {current_step} | DONE: {completed_str}\n"
            f"EXECUTE step '{current_step}' now. Call get_workflow_step(\"{active_workflow}\", \"{current_step}\", expand_lessons=true).\n"
            "</workflow-state>"
        )

    if output_parts:
        print("\n\n".join(output_parts))

    sys.exit(0)


if __name__ == "__main__":
    main()
