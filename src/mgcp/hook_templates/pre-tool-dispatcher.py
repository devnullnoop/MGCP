#!/usr/bin/env python3
"""PreToolUse dispatcher for MGCP — generic enforcement evaluator.

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
# `git` at a command boundary, possibly invoked by path (`/usr/bin/git`).
# Applied to raw text only when tokenizing fails.
_GIT_AT_BOUNDARY_RE = re.compile(r"(?:^|[\s;&|(){}])(?:[^\s;&|(){}]*/)?git(?=\s)")
# Commands that run another command: `git` after one of these is still the
# command being run, so the boundary has to survive them.
_COMMAND_WRAPPERS = {
    "env", "sudo", "doas", "time", "timeout", "nohup", "xargs",
    "command", "stdbuf", "nice", "ionice",
}
# A `VAR=value` prefix, which the shell strips before the command word.
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
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
            if at_command_start and tok.rsplit("/", 1)[-1] == "git":
                if _subcommand_after_git(tokens, i) in subcommands:
                    return True
            # The boundary survives anything the shell itself treats as
            # preamble: a `VAR=value` assignment, a wrapper like `sudo` or
            # `env`, and a wrapper's own flags. Dropping it on the first
            # token meant one word in front of the command -- `sudo git
            # push`, `env git commit`, or just the absolute path -- walked
            # straight through the gate with nothing malformed about it.
            at_command_start = (
                _ASSIGNMENT_RE.match(tok) is not None
                or tok.rsplit("/", 1)[-1] in _COMMAND_WRAPPERS
                or (at_command_start and tok.startswith("-"))
            )
    return False


def _trigger_matches(trigger: dict, tool_name: str, tool_input: dict) -> bool:
    t_tool = trigger.get("tool_name", "")
    if t_tool != "*" and t_tool != tool_name:
        return False
    cm = trigger.get("command_match")
    # Absent means "match the tool whatever the command is"; an empty dict
    # means a matcher with no type, which the schema rejects outright. Reading
    # both as absent made `"command_match": {}` match every call to the tool.
    if cm is None:
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


def _apology_match(text: str) -> tuple:
    """First matching pattern and the sentence it matched, or ("", "").

    A denial used to record only that the gate fired. The flagged sentence
    reached the audit log solely when the agent contested, which means the
    agent chose the evidence for every record that has any. Over 39 denials
    none carried a sentence and all 16 contests did, so the gate's precision
    could not be computed from its own log: the question "was that an
    apology" needs the text, and the text was never kept. Recording it here
    is the only point where it exists.
    """
    for pattern in APOLOGY_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        # The surrounding sentence, so a reader can judge the fire without
        # the transcript. Bounded both ways: the log is append-only.
        starts = [text.rfind(ch, 0, m.start()) for ch in ".!?\n"]
        start = max(starts) + 1 if max(starts) >= 0 else 0
        ends = [i for i in (text.find(ch, m.end()) for ch in ".!?\n") if i != -1]
        end = min(ends) + 1 if ends else len(text)
        return pattern.pattern, text[start:end].strip()[:300]
    return "", ""


def _latest_apology(transcript_path: str) -> tuple:
    """The most recent apology since the last user prompt, and whether it was answered.

    Returns ``(pattern, sentence, answered)``. ``answered`` is True when an
    ``add_lesson`` call appears LATER in the transcript than the apology, which
    is the only thing the gate accepts as payment.

    THE TRANSCRIPT IS THE ONLY SOURCE OF TRUTH HERE, on purpose. The gate used
    to decide "already answered" from ``turn_tools_called`` in
    ``workflow_state.json``, and that file is wrong for this question in two
    ways. It is shared: one file serves every project and every concurrent
    session, so one session's bookkeeping spoke for all of them. And it is
    agent-writable, so the thing being gated could edit the record of whether
    it had complied. A bound on ``turn_started_at`` read from the same file was
    worse again: any value newer than this session's own text made the walk
    stop immediately, the gate saw no text, and the tool call was allowed with
    no row written to ``gate_audit.jsonl``. A forged or future number, another
    session's turn, or a clock correction all reached that silent bypass of the
    only hook that can refuse a tool call. An adversarial audit found it by
    driving this hook with another session's bound and watching it allow.

    The transcript cannot be reached that way, and it already holds the answer.
    Walking backwards, an ``add_lesson`` seen before the apology is an
    ``add_lesson`` that happened after it in time, so one pass decides both
    questions. That also fixes the false deny the timestamp bound was added
    for: a message sent mid-turn resets ``turn_tools_called`` but changes
    nothing in the transcript, so an apology already paid for still reads as
    answered.

    Blocks are walked in reverse within each entry as well as across entries,
    because an assistant message can apologize in a text block and call
    ``add_lesson`` in a later block of the same message.

    Falls back to no match on any read or parse error, which allows the call.
    Enforcement here is a net rather than a tripwire.
    """
    if not transcript_path:
        return "", "", False
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except (OSError, IOError):
        return "", "", False

    answered = False
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "user":
            # Tool results are recorded as type=="user" entries whose content
            # is a list of tool_result blocks. Breaking on those lets any tool
            # call (even a denied one) clear the apology from view and reopen
            # the gate. Only a genuine user prompt (string content, or a list
            # containing a text block) ends the current turn.
            content = (entry.get("message") or {}).get("content")
            is_real_prompt = isinstance(content, str) or (
                isinstance(content, list)
                and any(
                    isinstance(b, dict) and b.get("type") == "text"
                    for b in content
                )
            )
            if is_real_prompt:
                break
            continue
        if etype != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = [b for b in content if isinstance(b, dict)]
        else:
            continue
        for block in reversed(blocks):
            btype = block.get("type")
            if btype == "tool_use" and block.get("name") == ADD_LESSON_TOOL:
                answered = True
            elif btype == "text":
                pattern, sentence = _apology_match(block.get("text") or "")
                if pattern:
                    return pattern, sentence, answered
    return "", "", False


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


def _get_staged_paths_not_deleted(cwd: str) -> list:
    """Staged paths that this commit adds, modifies, copies or renames.

    Deletions are left out. `git diff --cached --name-only` lists them, so a
    forbid-by-path rule built on that would refuse the very commit that REMOVES
    the forbidden file. The content scan has the same hazard and solves it the
    same way, by reading added lines only.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
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


def _get_staged_diff(cwd: str) -> str:
    """The staged diff as text, or "" if it cannot be read.

    Separate from _get_staged_files because content and paths answer
    different questions. A private project name or an absolute home
    directory arrives inside a sentence, so no amount of path matching sees
    it. Capped so a large commit cannot stall the hook.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--unified=0"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return ""
        return result.stdout[:4_000_000]
    except (OSError, subprocess.SubprocessError):
        return ""


def _added_lines(diff: str) -> str:
    """Only the lines this commit ADDS.

    Scanning the whole diff would refuse a commit for REMOVING a forbidden
    string, which is the opposite of what the rule wants.
    """
    out = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


def _check_coupling(staged, when_staged, require_one_of):
    triggering = [p for p in staged if any(fnmatch.fnmatch(p, w) for w in when_staged)]
    if not triggering:
        return True, []
    for p in staged:
        if any(fnmatch.fnmatch(p, r) for r in require_one_of):
            return True, triggering
    return False, triggering


def _safe_fnmatch(value: str, pattern: str) -> bool:
    try:
        return fnmatch.fnmatch(value, pattern)
    except Exception:
        return False


def _evaluate_precondition(
    pre: dict, state: dict, staged_files: list, tool_input: dict = None,
    staged_diff: str = "", staged_not_deleted: list = None,
):
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

    if pre_type == "staged_files_forbid":
        deny_globs = pre.get("deny_globs") or []
        if not deny_globs:
            return True, ""
        hits = [
            p for p in (staged_not_deleted or [])
            if any(_safe_fnmatch(p, g) for g in deny_globs)
        ]
        if not hits:
            return True, ""
        return False, (
            "staged files are forbidden by this rule:\n"
            + "\n".join(f"  - {h}" for h in hits[:20])
        )

    if pre_type == "staged_content_forbid":
        patterns = pre.get("patterns") or []
        if not patterns:
            return True, ""
        added = _added_lines(staged_diff or "")
        if not added:
            return True, ""
        hits = []
        for raw in patterns:
            try:
                found = re.search(raw, added, re.IGNORECASE)
            except re.error:
                continue  # a bad pattern must not refuse every commit
            if found:
                hits.append((raw, found.group(0)[:60]))
        if not hits:
            return True, ""
        return False, (
            "staged content matches a forbidden pattern:\n"
            + "\n".join(f"  - {m!r} matched {p!r}" for p, m in hits[:10])
        )

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

    # Unknown type — fail open, but on the record. A typo'd type disables the
    # whole rule silently, and the schema that would have rejected it never
    # sees a hand-edited rules file.
    _audit({"event": "skipped_precondition", "type": pre_type})
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

    The per-turn lists are coerced for the same reason one level down: a
    STRING turn_tools_called makes `name in called` a substring test, so
    any text mentioning the required tool satisfies every
    tool_called_this_turn precondition, and a string turn_bypass_scopes of
    "*" becomes the set {"*"} -- bypass-all written by whatever last
    touched the file.
    """
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for key in ("turn_tools_called", "turn_bypass_scopes"):
                    if key in loaded and not isinstance(loaded[key], list):
                        loaded[key] = []
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
    # Whether an apology was already paid for is read from the transcript, not
    # from turn_tools_called. That list lives in a shared, agent-writable file,
    # so it answered for every concurrent session at once and the gated agent
    # could edit it. _latest_apology decides it from the transcript instead.
    if (
        tool_name not in (ADD_LESSON_TOOL, ADJUDICATE_TOOL)
        and tool_name not in DISCOVERY_TOOLS
        and APOLOGY_BYPASS_SCOPE not in bypass_scopes
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
            matched_pattern, flagged_sentence, answered = _latest_apology(transcript_path)
            if matched_pattern and not answered:
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
                          "session_id": session_id,
                          "pattern": matched_pattern,
                          "flagged_sentence": flagged_sentence})
    elif (
        tool_name == ADD_LESSON_TOOL
        and APOLOGY_BYPASS_SCOPE not in bypass_scopes
        and ADD_LESSON_TOOL not in (state.get("turn_tools_called") or [])
    ):
        transcript_path = hook_input.get("transcript_path", "")
        matched_pattern, flagged_sentence, answered = _latest_apology(transcript_path)
        if matched_pattern and not answered:
            # A compliance is a label too: it says this fire was accepted.
            # Without the sentence it is an unexaminable vote.
            _audit({"event": "comply", "gate": "apology",
                    "session_id": session_id,
                    "pattern": matched_pattern,
                    "flagged_sentence": flagged_sentence,
                    "lesson_id": (tool_input or {}).get("id", "")})

    if not rules:
        _allow()

    denials = []
    staged_files = None  # lazy
    staged_diff = None  # lazy
    staged_not_deleted = None  # lazy

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
        types = {(p or {}).get("type") for p in preconditions}
        # Both are one subprocess each, so read them once per tool call at
        # most, and only when a matched rule actually asks.
        if staged_files is None and types & {"staged_files_coupling", "staged_files_forbid"}:
            staged_files = _get_staged_files(project_dir)
        if staged_diff is None and "staged_content_forbid" in types:
            staged_diff = _get_staged_diff(project_dir)
        if staged_not_deleted is None and "staged_files_forbid" in types:
            staged_not_deleted = _get_staged_paths_not_deleted(project_dir)

        unsatisfied = []
        for pre in preconditions:
            try:
                ok, detail = _evaluate_precondition(
                    pre or {}, state, staged_files or [], tool_input,
                    staged_diff or "", staged_not_deleted or [],
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
