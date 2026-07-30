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

GATE_AUDIT_FILE = Path(
    os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
) / "gate_audit.jsonl"


def _audit(event: dict) -> None:
    """Append one line to the gate audit log. Fails silently: the audit is
    an instrument, and losing a line must never change an enforcement
    decision. Before this existed a denied tool call left no trace at all."""
    try:
        import datetime

        event["ts"] = datetime.datetime.now(datetime.UTC).isoformat()
        GATE_AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GATE_AUDIT_FILE, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


SHELL_SEPARATORS = {"&&", "||", "&", ";", ";;", "|", "(", ")", "{", "}"}
# `git` at a command boundary. Applied to raw text only when tokenizing fails.
_GIT_AT_BOUNDARY_RE = re.compile(r"(?:^|[\s;&|(){}])git(?=\s)")
# Global flags that consume the NEXT token as their value, so the subcommand
# sits one slot further along: `git -C /path commit` is still a commit.
_GIT_VALUE_FLAGS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--config-env", "--super-prefix",
}
# How far past `git` to look for the subcommand in the raw-text fallback.
_GIT_RAW_LOOKAHEAD = 6
BYPASS_ALL = "*"
APOLOGY_BYPASS_SCOPE = "apology"
ADD_LESSON_TOOL = "mcp__mgcp__add_lesson"
ADJUDICATE_TOOL = "mcp__mgcp__adjudicate_apology_gate"

# Tool-discovery calls, which a deferred-tool harness must make BEFORE it can
# call add_lesson at all. Gating discovery gates the exits themselves, which
# turns the gate into a deadlock with the key locked inside -- observed on
# 2026-07-30 taking down a whole verification run. These calls cannot mutate
# state, so exempting them costs nothing. Stateless by design: the earlier
# attempt at a denial counter with an advisory-degrade valve was measured and
# cut, because it disabled every OTHER enforcement rule as a side effect.
DISCOVERY_TOOLS = {"ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool"}

# Apology markers that must immediately trigger an add_lesson call.
# Rule: if the assistant's current turn contains any of these patterns,
# the very next tool call must be add_lesson — anything else is denied.
# The gate clears naturally on the next user prompt (turn_tools_called reset).
APOLOGY_PATTERNS = [
    re.compile(r"\bsorry\b", re.IGNORECASE),
    re.compile(r"\bmy bad\b", re.IGNORECASE),
    re.compile(r"\byou'?re right\b", re.IGNORECASE),
    re.compile(r"\byou are right\b", re.IGNORECASE),
    re.compile(r"\bmy mistake\b", re.IGNORECASE),
    re.compile(r"\bmy apolog(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\bapologi[sz]e\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Tokenization / matchers
# ---------------------------------------------------------------------------


def _tokenize(command: str) -> list:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _scan_git_subcommand_raw(line: str, subcommands: list) -> bool:
    """Last-resort scan of raw text for `git <sub>` at a command boundary.

    Used only when the line cannot be tokenized. Detection must not depend on
    the command being well-formed: an unterminated quote is author-controlled
    text, so treating it as "not a git command" turns any apostrophe in a
    commit message into a way through the gate.
    """
    for match in _GIT_AT_BOUNDARY_RE.finditer(line):
        for tok in line[match.end():].split()[:_GIT_RAW_LOOKAHEAD]:
            if tok in subcommands:
                return True
    return False


def _subcommand_after_git(tokens: list, i: int):
    """The subcommand token following `git` at index i, skipping global flags.
    Without this, `git -C /path commit` read as subcommand `-C` and matched
    nothing, so prefixing any gated command with `-C .` skipped it."""
    j = i + 1
    while j < len(tokens) and tokens[j].startswith("-"):
        flag = tokens[j]
        j += 1
        if flag in _GIT_VALUE_FLAGS and j < len(tokens):
            j += 1
    return tokens[j] if j < len(tokens) else None


def _detect_git_subcommand(command: str, subcommands: list) -> bool:
    """Scanned line by line. A newline is a command separator in shell, but
    shlex with whitespace_split consumes it as ordinary whitespace, so a single
    token stream cannot tell `cd /x` NEWLINE `git commit` from `cd /x git
    commit` -- and in that stream `git` no longer sits at a command boundary,
    so the trigger silently stopped matching.

    Lines that fail to tokenize fall back to a raw scan rather than being
    skipped: both paths fail closed, because a command this function cannot
    read is not evidence that the command is safe.
    """
    for line in command.splitlines():
        if not line.strip():
            continue
        try:
            tokens = _tokenize(line)
        except ValueError:
            if _scan_git_subcommand_raw(line, subcommands):
                return True
            continue
        at_command_start = True
        for i, tok in enumerate(tokens):
            if tok in SHELL_SEPARATORS:
                at_command_start = True
                continue
            if at_command_start and tok == "git":
                if _subcommand_after_git(tokens, i) in subcommands:
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
# Apology gate (hardcoded — trigger is assistant text, not a tool arg)
# ---------------------------------------------------------------------------


def _has_apology(text: str) -> bool:
    return any(p.search(text) for p in APOLOGY_PATTERNS)


def _current_turn_assistant_text(transcript_path: str) -> str:
    """Concatenate assistant text blocks emitted since the most recent user turn.

    Walks the transcript JSONL backwards stopping at the first user entry.
    Falls back to empty string on any read/parse error (fail open).
    """
    if not transcript_path:
        return ""
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except (OSError, IOError):
        return ""
    parts = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        etype = entry.get("type")
        if etype == "user":
            break
        if etype != "assistant":
            continue
        msg = entry.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
        elif isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


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


def _evaluate_precondition(pre: dict, state: dict, staged_files: list, tool_input: dict = None):
    tool_input = tool_input or {}
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

    if pre_type == "tool_input_glob":
        field = pre.get("field", "")
        deny_globs = pre.get("deny_globs") or []
        if not field or not deny_globs:
            return True, ""
        value = tool_input.get(field)
        if not isinstance(value, str):
            return True, ""
        for pattern in deny_globs:
            try:
                if fnmatch.fnmatch(value, pattern):
                    return (
                        False,
                        f"tool_input.{field} = {value!r} matches deny pattern {pattern!r}",
                    )
            except Exception:
                continue
        return True, ""

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
    """Load per-turn state, or {} if it is unusable.

    The isinstance check is load-bearing: workflow_state.json is
    agent-writable, and a valid-JSON non-object (an array, a string, a
    number) parses cleanly and then raises on .get() -- crashing the hook
    PAST the rule loop, which the harness reads as allow. Malformed state
    must degrade to "no state", never to "no enforcement".
    """
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                return loaded
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _allow():
    sys.exit(0)


def _fail_open(exc: Exception):
    """Last-resort handler. The hook fails open by design -- enforcement is a
    net, not a tripwire -- but a silent fail-open defeats the audit log, so
    the crash is recorded before the call is allowed."""
    _audit({"event": "hook_error", "error": repr(exc)[:300]})
    sys.exit(0)


def _deny(reasons: list, audit: dict | None = None):
    if audit is not None:
        _audit({"event": "deny", **audit})
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
    state = _load_state()
    bypass_scopes = set(state.get("turn_bypass_scopes") or [])
    if BYPASS_ALL in bypass_scopes:
        _allow()

    # Apology gate: if this turn's assistant text contains an apology and
    # add_lesson hasn't been called yet, the only permitted tool is
    # add_lesson itself. Rationale: MEMORY.md rule that apologies must
    # immediately trigger a knowledge write, promoted from passive note
    # to hard enforcement. Bypass with MGCP_BYPASS:apology. Runs
    # independently of enforcement_rules.json — this is a first-class
    # gate, not a data rule, because its trigger is assistant text not a
    # tool arg.
    session_id = hook_input.get("session_id", "")
    if (
        tool_name not in (ADD_LESSON_TOOL, ADJUDICATE_TOOL)
        and tool_name not in DISCOVERY_TOOLS
        and APOLOGY_BYPASS_SCOPE not in bypass_scopes
        and ADD_LESSON_TOOL not in (state.get("turn_tools_called") or [])
    ):
        # An adjudication speaks only for the session that recorded it:
        # workflow_state.json is shared across concurrent sessions, so an
        # unscoped verdict would open every one of them.
        # Defensive: workflow_state.json is agent-writable, and a crash here
        # means rc=1 with empty stdout, which the harness reads as ALLOW --
        # a silent, unaudited bypass. A malformed value must read as "no
        # adjudication" (fail closed: keep denying), never as an exception.
        adjudication = state.get("turn_apology_adjudication")
        if not isinstance(adjudication, dict):
            adjudication = {}
        adj_session = adjudication.get("session_id")
        if not isinstance(adj_session, str):
            adj_session = ""
        # Exact match only. A lenient "" means "applies to everyone", which a
        # forged or malformed session_id could reach by type confusion; the
        # single-session case still works because both sides are then "".
        adj_applies = (
            adjudication.get("verdict") == "not_apology"
            and adj_session == (session_id if isinstance(session_id, str) else "")
        )
        if not adj_applies:
            transcript_path = hook_input.get("transcript_path", "")
            text = _current_turn_assistant_text(transcript_path)
            if _has_apology(text):
                _deny([
                    "[apology-requires-add-lesson] You apologized in this "
                    "turn. Two exits: (1) COMPLY -- call "
                    "mcp__mgcp__add_lesson capturing what you should do "
                    "differently next time; or (2) CONTEST -- call "
                    "mcp__mgcp__adjudicate_apology_gate with the flagged "
                    "text, a verdict and your reasoning, which goes on the "
                    "audit record. Bypass: include MGCP_BYPASS:apology in "
                    "the next user prompt."
                ], audit={"gate": "apology", "tool_denied": tool_name,
                          "session_id": session_id})
    elif (
        tool_name == ADD_LESSON_TOOL
        and APOLOGY_BYPASS_SCOPE not in bypass_scopes
        and ADD_LESSON_TOOL not in (state.get("turn_tools_called") or [])
    ):
        transcript_path = hook_input.get("transcript_path", "")
        if _has_apology(_current_turn_assistant_text(transcript_path)):
            _audit({"event": "comply", "gate": "apology",
                    "session_id": session_id,
                    "lesson_id": (tool_input or {}).get("id", "")})

    if not rules:
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
                ok, detail = _evaluate_precondition(
                    pre or {}, state, staged_files or [], tool_input
                )
            except Exception:
                ok, detail = True, ""
            if not ok:
                unsatisfied.append(detail)

        if unsatisfied:
            reason = rule.get("deny_reason") or f"Rule '{rule.get('name', '?')}' violated"
            details = "\n".join(unsatisfied)
            denials.append(f"[{rule.get('name', '?')}] {reason}\n{details}")

    if denials:
        _deny(denials, audit={
            "gate": "rules", "tool_denied": tool_name, "session_id": session_id,
            "rules": [d.split("]")[0].lstrip("[") for d in denials]})
    _allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never let a crash silently allow a tool call
        _fail_open(exc)
