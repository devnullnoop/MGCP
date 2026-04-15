#!/usr/bin/env python3
"""PreToolUse dispatcher for MGCP v2.3.

First ENFORCING hook. Prior hooks (SessionStart, UserPromptSubmit,
PostToolUse, PreCompact) are all advisory — they inject text that the LLM
may skim or ignore. This hook can refuse a tool call outright by emitting
``permissionDecision: "deny"``.

Currently enforces one rule:

    Before a Bash call containing ``git commit`` or ``git push``, the LLM
    must have called ``mcp__mgcp__query_lessons`` in the current turn.

Rationale: ``query-before-git-operations`` has failed v1→v4 across months
despite keyword gates and system reminders. Enforcement is the fix.

Bypass: if the user's prompt contains ``MGCP_BYPASS`` (case insensitive),
UserPromptSubmit sets a per-turn bypass flag and this hook allows the
commit through. This keeps one-shot "just commit it" workflows usable.

State flow:
- UserPromptSubmit resets ``turn_query_lessons_called`` to False and sets
  ``turn_bypass`` based on the prompt.
- PostToolUse flips ``turn_query_lessons_called`` to True when
  ``mcp__mgcp__query_lessons`` runs.
- This hook reads both flags.

The hook falls open (allows) on any error reading state — it is a safety
net, not a tripwire. Losing enforcement is always preferable to blocking
a tool call because of a malformed JSON file.
"""
import json
import os
import shlex
import sys
from pathlib import Path

STATE_FILE = Path(
    os.environ.get(
        "MGCP_STATE_FILE",
        str(Path.home() / ".mgcp" / "workflow_state.json"),
    )
)

# Tokens (as produced by shlex with punctuation_chars) that reset
# "we are at the start of a new command". Distinguishes `foo && git commit`
# (real commit) from `grep 'git commit' file` (string inside an argument).
SHELL_SEPARATORS = {"&&", "||", "&", ";", ";;", "|", "(", ")", "{", "}"}

ENFORCED_GIT_SUBCOMMANDS = {"commit", "push"}


def _tokenize(command: str) -> list[str]:
    """Tokenize a shell command, splitting on whitespace AND shell operators
    (``;``, ``&&``, ``||``, ``|``, ``(``, ``)``) while keeping quoted
    strings intact as single tokens.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _command_invokes_enforced_git(command: str) -> bool:
    """True iff ``command`` actually invokes ``git commit`` or ``git push``
    as a shell command — not merely contains the string.

    Tokenizes with shlex (punctuation_chars=True) so quoted content stays
    as a single token and shell operators like ``;`` and ``&&`` are split
    into their own tokens. Walks tokens and matches only when ``git``
    appears as the first token of a new command (start of string or
    immediately after a shell separator).
    """
    try:
        tokens = _tokenize(command)
    except ValueError:
        # Unterminated quotes etc. — fail open, don't block on parse errors.
        return False

    at_command_start = True
    for i, tok in enumerate(tokens):
        if tok in SHELL_SEPARATORS:
            at_command_start = True
            continue
        if at_command_start and tok == "git":
            if i + 1 < len(tokens) and tokens[i + 1] in ENFORCED_GIT_SUBCOMMANDS:
                return True
        at_command_start = False
    return False


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _allow():
    """Default: do nothing, tool call proceeds."""
    sys.exit(0)


def _deny(reason: str):
    """Block the tool call with a reason the LLM will see on its next turn."""
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload))
    sys.exit(0)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _allow()

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {}) or {}

    # Only gate Bash calls; other tools pass through.
    if tool_name != "Bash":
        _allow()

    command = str(tool_input.get("command", ""))
    if not _command_invokes_enforced_git(command):
        _allow()

    state = _load_state()

    # Bypass takes precedence — user explicitly opted out of enforcement.
    if state.get("turn_bypass", False):
        _allow()

    if state.get("turn_query_lessons_called", False):
        _allow()

    _deny(
        "MGCP enforcement: git commit/push requires a query_lessons call "
        "in this turn first.\n"
        "Call mcp__mgcp__query_lessons(task_description=\"git commit\") "
        "now, read the results, then retry the git command.\n"
        "To bypass once, include the token MGCP_BYPASS anywhere in your "
        "next user prompt."
    )


if __name__ == "__main__":
    main()
