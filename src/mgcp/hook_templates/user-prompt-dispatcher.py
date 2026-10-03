#!/usr/bin/env python3
"""UserPromptSubmit dispatcher for MGCP.

Responsibilities:
1. Read keyword gates and the terse routing block from intent_config.json.
2. Fire hard-stop gates for high-consequence intents (git, session_end).
3. Re-inject the terse routing block on every message so it survives
   context compaction.
4. Surface scheduled reminders (counter/timer-based).
5. Inject active workflow state.
6. Reset per-turn enforcement state (``turn_tools_called=[]`` consumed by
   the PreToolUse evaluator) and parse the ``MGCP_BYPASS[:scope]`` opt-out
   tokens into ``turn_bypass_scopes``.

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


def _short_delta(seconds: float) -> str:
    """Elapsed time in as few characters as read clearly, with minutes.

    `relative_age` in the package calls anything under an hour "now". That is
    right for the age of a note and wrong here, because the gap between two
    messages is most useful at minute resolution: six minutes and three hours
    mean different things about what the reader can assume. The two agree from
    one hour upward, and tests/test_user_prompt_clock.py checks that they do.
    """
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _clock_line(state: dict, session_id: str, now: float | None = None) -> str:
    """The current time, the gap since the last message, and the session length.

    A model reads a transcript with no sense of elapsed time. Without this, a
    reply written three hours later reads exactly like one written in ten
    seconds, and "we just did X" stops being true without anything saying so.

    Never raises. A clock that can fail is worse than no clock, because the
    whole injected block travels with it.
    """
    now = now if now is not None else time.time()
    local = time.strftime("%H:%M %a %-d %b", time.localtime(now))
    parts = [f"⌚ {local}"]

    previous = state.get("turn_started_at")
    same_session = state.get("turn_session_id") == session_id
    if isinstance(previous, (int, float)) and same_session:
        parts.append(f"{_short_delta(now - previous)} since your last message")
    else:
        parts.append("first message of this session")

    started = state.get("session_started_at")
    if isinstance(started, (int, float)) and same_session and now - started >= 60:
        parts.append(f"{_short_delta(now - started)} into this session")

    return "<time>" + " · ".join(parts) + "</time>"


def _load_state() -> dict:
    """Load workflow/reminder state from file."""
    defaults = {
        "current_call_count": 0,
        "remind_at_call": 0,
        "remind_at_time": 0,
        "reminder_message": "",
        "lesson_ids": [],
        "workflow_step": "",
        "active_workflow": None,
        "current_step": None,
        "workflow_complete": False,
        "steps_completed": [],
    }
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                state = json.load(f)
            # The isinstance check is load-bearing, and pre-tool-dispatcher
            # has carried it since v2.4 while these two hooks did not.
            # workflow_state.json is agent-writable, and a valid-JSON
            # non-object (an array, a string, a number) parses cleanly and
            # then raises on the key assignment below. This hook has no
            # top-level handler, so that reached the operator as
            # "TypeError: list indices must be integers" on every message,
            # and the clock it claims cannot take the block down with it was
            # loaded outside that try. Malformed state degrades to defaults.
            if not isinstance(state, dict):
                return defaults
            for key, value in defaults.items():
                if key not in state:
                    state[key] = value
            return state
    except (json.JSONDecodeError, IOError, OSError, TypeError, ValueError):
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
    session_id = hook_input.get("session_id", "") or ""

    # The clock is built before anything else and printed first. It must not be
    # able to take the rest of the block down with it.
    now = time.time()
    clock_state = _load_state()
    try:
        output_parts.append(_clock_line(clock_state, session_id, now))
    except Exception:
        pass

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

    # Per-turn enforcement state (consumed by pre-tool-dispatcher.py).
    # Resets every message so each turn gets fresh accounting.
    # - turn_tools_called: list of tool_name strings; PostToolUse appends
    #   to this list. Preconditions of type "tool_called_this_turn" check
    #   membership.
    # - turn_bypass_scopes: list of scope strings. "*" disables all rules;
    #   named scopes ("git", "docs") disable rules with matching
    #   bypass_scope. Parsed from MGCP_BYPASS and MGCP_BYPASS:<scope>
    #   tokens in the prompt.
    state["turn_tools_called"] = []
    # - turn_session_id: this session's harness id, recorded so the
    #   adjudicate_apology_gate MCP tool can scope its verdict to this session
    #   without the model having to know a value nothing ever tells it. The
    #   PreToolUse gate requires an exact match, so before this was written
    #   here the gate's contest exit could never open.
    # Read before this line overwrites it, so the clock above saw the previous
    # turn's value.
    if state.get("turn_session_id") != session_id:
        state["session_started_at"] = now
    state["turn_started_at"] = now
    state["turn_session_id"] = session_id
    bypass_scopes = []
    for match in re.finditer(
        r"MGCP_BYPASS(?::([A-Za-z0-9_-]+))?", prompt, re.IGNORECASE
    ):
        scope = match.group(1)
        # Lowercased: the token itself matches case-insensitively, so a user
        # who types MGCP_BYPASS:GIT gets a scope no rule's lowercase
        # bypass_scope can ever equal -- a deliberate opt-out discarded in
        # silence while the deny message kept advertising the mechanism.
        bypass_scopes.append(scope.lower() if scope else "*")
    state["turn_bypass_scopes"] = bypass_scopes
    # v2.11: an adjudication only ever opens the gate for ITS turn.
    state.pop("turn_apology_adjudication", None)

    if bypass_scopes:
        # A bypass token is a human decision; it goes on the gate audit
        # record like every other gate event. Fails silently.
        try:
            import datetime as _dt

            audit_path = Path(
                os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
            ) / "gate_audit.jsonl"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(audit_path, "a") as f:
                f.write(json.dumps({
                    "event": "human_bypass",
                    "scopes": bypass_scopes,
                    "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                }) + "\n")
        except Exception:
            pass

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
