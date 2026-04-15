#!/usr/bin/env python3
"""PreToolUse dispatcher for MGCP v2.4 — generic enforcement evaluator.

This hook is **data-driven**. It reads enforcement rules from
``~/.mgcp/enforcement_rules.json`` (override with ``MGCP_DATA_DIR``) and
applies every enabled, triggered, non-bypassed rule to each tool call. If
any rule's preconditions are unsatisfied, the hook emits
``permissionDecision: "deny"`` and the Claude Code harness refuses the
tool.

Adding a new enforcement rule means calling an MCP tool (or editing the
JSON) — never editing hook code. The canonical schema and default rules
live in ``src/mgcp/enforcement.py``; this hook is stdlib-only (no
``mgcp`` import) and implements the same evaluator semantics. Tests in
``tests/test_pre_tool_dispatcher.py`` and ``tests/test_enforcement.py``
exercise the shared behavioral contract.

Key invariants:

- **Fails open.** Any parse error in a rule, trigger, or precondition
  *skips* that rule rather than blocking the tool call. Enforcement is a
  safety net, not a tripwire.
- **Bypass is per-scope.** Each rule names a ``bypass_scope`` (e.g.
  ``"git"``). The user's prompt may contain ``MGCP_BYPASS:<scope>`` to
  disable one scope or bare ``MGCP_BYPASS`` to disable all. The
  UserPromptSubmit hook parses these tokens into ``turn_bypass_scopes``
  on workflow_state.json.
- **Per-turn tool accounting.** The ``turn_tools_called`` list on
  workflow_state.json is reset each turn by UserPromptSubmit and appended
  to by PostToolUse. Preconditions of type ``tool_called_this_turn``
  check membership.
"""
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

STATE_FILE = Path(
    os.environ.get(
        "MGCP_STATE_FILE",
        str(Path.home() / ".mgcp" / "workflow_state.json"),
    )
)

ENFORCEMENT_CONFIG = Path(
    os.environ.get(
        "MGCP_ENFORCEMENT_CONFIG",
        str(
            Path(os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp")))
            / "enforcement_rules.json"
        ),
    )
)

SHELL_SEPARATORS = {"&&", "||", "&", ";", ";;", "|", "(", ")", "{", "}"}
BYPASS_ALL = "*"


# ---------------------------------------------------------------------------
# Tokenization / matchers
# ---------------------------------------------------------------------------


def _tokenize(command: str) -> list:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _detect_git_subcommand(command: str, subcommands: list) -> bool:
    try:
        tokens = _tokenize(command)
    except ValueError:
        return False
    at_command_start = True
    for i, tok in enumerate(tokens):
        if tok in SHELL_SEPARATORS:
            at_command_start = True
            continue
        if at_command_start and tok == "git":
            if i + 1 < len(tokens) and tokens[i + 1] in subcommands:
                return True
        at_command_start = False
    return False


def _trigger_matches(trigger: dict, tool_name: str, tool_input: dict) -> bool:
    t_tool = trigger.get("tool_name", "")
    if t_tool != "*" and t_tool != tool_name:
        return False
    cm = trigger.get("command_match")
    if not cm:
        return True
    if tool_name != "Bash":
        return False
    command = str(tool_input.get("command", ""))
    cm_type = cm.get("type", "")
    if cm_type == "git_subcommand":
        return _detect_git_subcommand(command, cm.get("subcommands") or [])
    if cm_type == "regex":
        try:
            return re.search(cm.get("pattern", ""), command) is not None
        except re.error:
            return False
    if cm_type == "contains":
        return cm.get("pattern", "") in command
    return False


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def _get_staged_files(cwd: str) -> list:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _check_coupling(staged, when_staged, require_one_of):
    triggering = [p for p in staged if any(fnmatch.fnmatch(p, w) for w in when_staged)]
    if not triggering:
        return True, []
    for p in staged:
        if any(fnmatch.fnmatch(p, r) for r in require_one_of):
            return True, triggering
    return False, triggering


def _evaluate_precondition(pre: dict, state: dict, staged_files: list):
    called = state.get("turn_tools_called") or []
    pre_type = pre.get("type", "")

    if pre_type == "tool_called_this_turn":
        name = pre.get("tool_name", "")
        if name in called:
            return True, ""
        return False, f"Required tool not called this turn: {name}"

    if pre_type == "tool_not_called_this_turn":
        name = pre.get("tool_name", "")
        if name not in called:
            return True, ""
        return False, f"Forbidden tool called this turn: {name}"

    if pre_type == "staged_files_coupling":
        unsatisfied = []
        for c in pre.get("couplings") or []:
            when = c.get("when_staged") or []
            req = c.get("require_one_of") or []
            if not when or not req:
                continue
            ok, triggering = _check_coupling(staged_files, when, req)
            if not ok:
                unsatisfied.append(
                    f"  - staged: {', '.join(triggering)} -> require one of: {', '.join(req)}"
                )
        if not unsatisfied:
            return True, ""
        return False, "Doc-coupling violations:\n" + "\n".join(unsatisfied)

    # Unknown type — fail open
    return True, ""


# ---------------------------------------------------------------------------
# Config + state
# ---------------------------------------------------------------------------


def _load_rules() -> list:
    """Load enforcement rules. Returns [] on any failure (fail open)."""
    try:
        if not ENFORCEMENT_CONFIG.exists():
            return []
        with open(ENFORCEMENT_CONFIG) as f:
            data = json.load(f)
        rules = data.get("rules") or []
        return rules if isinstance(rules, list) else []
    except (json.JSONDecodeError, OSError, ValueError):
        return []


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _allow():
    sys.exit(0)


def _deny(reasons: list):
    header = "MGCP enforcement blocked this tool call:\n\n"
    body = "\n\n".join(reasons)
    footer = (
        "\n\nTo bypass specific rules only, include "
        "MGCP_BYPASS:<scope> in your next user prompt "
        "(e.g. MGCP_BYPASS:git). Bare MGCP_BYPASS disables all rules."
    )
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": header + body + footer,
        }
    }
    print(json.dumps(payload))
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _allow()

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {}) or {}
    project_dir = hook_input.get("cwd") or os.getcwd()

    rules = _load_rules()
    if not rules:
        _allow()

    state = _load_state()
    bypass_scopes = set(state.get("turn_bypass_scopes") or [])
    if BYPASS_ALL in bypass_scopes:
        _allow()

    denials = []
    staged_files = None  # lazy

    for rule in rules:
        try:
            if not rule.get("enabled", True):
                continue
            scope = rule.get("bypass_scope", "")
            if scope in bypass_scopes:
                continue
            trigger = rule.get("trigger") or {}
            if not _trigger_matches(trigger, tool_name, tool_input):
                continue
        except Exception:
            continue  # malformed rule -> fail open

        preconditions = rule.get("preconditions") or []
        needs_staged = any(
            (p or {}).get("type") == "staged_files_coupling" for p in preconditions
        )
        if needs_staged and staged_files is None:
            staged_files = _get_staged_files(project_dir)

        unsatisfied = []
        for pre in preconditions:
            try:
                ok, detail = _evaluate_precondition(pre or {}, state, staged_files or [])
            except Exception:
                ok, detail = True, ""
            if not ok:
                unsatisfied.append(detail)

        if unsatisfied:
            reason = rule.get("deny_reason") or f"Rule '{rule.get('name', '?')}' violated"
            details = "\n".join(unsatisfied)
            denials.append(f"[{rule.get('name', '?')}] {reason}\n{details}")

    if denials:
        _deny(denials)
    _allow()


if __name__ == "__main__":
    main()
