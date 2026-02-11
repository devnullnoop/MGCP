#!/usr/bin/env python3
"""PostToolUse hook - error detection and self-correction for Bash commands.

Scans Bash tool_response for error patterns (Traceback, FAILED, fatal:, etc.)
and injects an enforcement prompt telling the LLM to analyze, fix, and
optionally capture a lesson.

Cooldown: fires at most once per 60 seconds to avoid noise during debugging.
"""
import json
import re
import sys
import time
from pathlib import Path

# Error patterns to detect (compiled for performance)
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

# Patterns that indicate intentional error testing — suppress hook
IGNORE_CONTEXTS = [
    re.compile(r"grep.*-[a-zA-Z]*c", re.IGNORECASE),  # grep -c (count, exit 1 = no match)
    re.compile(r"^\s*#.*test", re.IGNORECASE),  # commented test references
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
    """Extract the text output from tool_response."""
    response = hook_input.get("tool_response", "")
    if isinstance(response, dict):
        # Bash tool response may have stdout/result fields
        for key in ("stdout", "result", "output", "content"):
            if key in response:
                return str(response[key])
        return json.dumps(response)
    return str(response)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    # Only process Bash tool calls
    if hook_input.get("tool_name") != "Bash":
        sys.exit(0)

    output = _extract_output(hook_input)
    if not output:
        sys.exit(0)

    # Check for ignore contexts (intentional error testing)
    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    for pattern in IGNORE_CONTEXTS:
        if pattern.search(command):
            sys.exit(0)

    # Scan for error patterns
    matched_patterns = []
    for pattern in ERROR_PATTERNS:
        if pattern.search(output):
            matched_patterns.append(pattern.pattern)

    if not matched_patterns:
        sys.exit(0)

    # Cooldown check
    state = _load_state()
    now = time.time()
    if now - state.get("last_fire", 0) < COOLDOWN_SECONDS:
        sys.exit(0)

    # Fire
    state["last_fire"] = now
    _save_state(state)

    print("""You are a self-correcting agent. An error was detected in your last command.

1. IDENTIFY the error: what command failed and why.
2. FIX the root cause, not the symptom.
3. If this error could recur across sessions, CALL add_lesson with:
   - trigger: the error pattern or scenario
   - action: how to avoid or fix it

Do NOT move on without considering step 3.""")


if __name__ == "__main__":
    main()