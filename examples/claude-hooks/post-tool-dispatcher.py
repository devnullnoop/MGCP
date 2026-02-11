#!/usr/bin/env python3
"""PostToolUse dispatcher for MGCP.

Routes post-tool actions by tool_name:
- Edit/Write: knowledge-capture checkpoint (patterns, gotchas, couplings)
- Bash: error detection with cooldown (self-correction + lesson capture)

Single hook, single matcher (no filter — handles all tools internally).
"""
import json
import re
import sys
import time
from pathlib import Path

# --- Bash error detection ---

ERROR_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE),
    re.compile(r"^error:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^fatal:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"FAILED", re.IGNORECASE),
    re.compile(r"command not found"),
    re.compile(r"Permission denied", re.IGNORECASE),
    re.compile(r"ModuleNotFoundError"),
    re.compile(r"ImportError"),
    re.compile(r"SyntaxError"),
    re.compile(r"NameError"),
    re.compile(r"TypeError"),
    re.compile(r"AttributeError"),
    re.compile(r"KeyError"),
    re.compile(r"ValueError"),
    re.compile(r"FileNotFoundError"),
    re.compile(r"ConnectionRefusedError"),
    re.compile(r"panic:", re.IGNORECASE),
]

COOLDOWN_SECONDS = 60
STATE_FILE = Path.home() / ".mgcp" / "error_detector_state.json"


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        pass
    return {"last_fire": 0}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except (IOError, OSError):
        pass


def _extract_output(hook_input: dict) -> str:
    """Extract text output from tool_response."""
    response = hook_input.get("tool_response", "")
    if isinstance(response, dict):
        for key in ("stdout", "result", "output", "content"):
            if key in response:
                return str(response[key])
        return json.dumps(response)
    return str(response)


# --- Handlers ---

def handle_edit_write():
    """Knowledge-capture checkpoint after code changes."""
    print("""STOP. You are a knowledge-capturing agent. Every code change is a potential lesson.

1. LIST the file(s) you just changed.
2. STATE any pattern, gotcha, or coupling you discovered.
3. CALL the appropriate MGCP tool for each finding:
   - Pattern/gotcha: add_catalogue_item(item_type="arch", ...)
   - Files coupled: add_catalogue_item(item_type="coupling", ...)
   - Decision made: add_catalogue_item(item_type="decision", ...)

Do NOT proceed until steps 1-3 are complete.""")


def handle_bash(hook_input: dict):
    """Error detection with cooldown for Bash commands."""
    output = _extract_output(hook_input)
    if not output:
        return

    # Scan for error patterns
    for pattern in ERROR_PATTERNS:
        if pattern.search(output):
            break
    else:
        return  # No errors found

    # Cooldown check
    state = _load_state()
    now = time.time()
    if now - state.get("last_fire", 0) < COOLDOWN_SECONDS:
        return

    state["last_fire"] = now
    _save_state(state)

    print("""You are a self-correcting agent. An error was detected in your last command.

1. IDENTIFY the error: what command failed and why.
2. FIX the root cause, not the symptom.
3. If this error could recur across sessions, CALL add_lesson with:
   - trigger: the error pattern or scenario
   - action: how to avoid or fix it

Do NOT move on without considering step 3.""")


def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")

    if tool_name in ("Edit", "Write"):
        handle_edit_write()
    elif tool_name == "Bash":
        handle_bash(hook_input)

    sys.exit(0)


if __name__ == "__main__":
    main()
