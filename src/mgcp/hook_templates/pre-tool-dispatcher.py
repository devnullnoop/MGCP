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
    except Exception:  # mgcp: allow-broad-except the audit log must never fail the hook it records
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
        if _line_runs_git_subcommand(tokens, subcommands):
            return True
    return False


def _starts_a_command(tok: str, at_command_start: bool) -> bool:
    """Whether the NEXT token begins a command, given this one.

    The boundary survives anything the shell itself treats as preamble: a
    ``VAR=value`` assignment, a wrapper such as ``sudo`` or ``env``, and a
    wrapper's own flags. Dropping it on the first token meant one word in front
    of the command, like ``sudo git push``, ``env git commit``, or just the
    absolute path, walked straight through the gate with nothing malformed
    about it.
    """
    return (
        _ASSIGNMENT_RE.match(tok) is not None
        or tok.rsplit("/", 1)[-1] in _COMMAND_WRAPPERS
        or (at_command_start and tok.startswith("-"))
    )


def _line_runs_git_subcommand(tokens: list, subcommands: list) -> bool:
    """True when one tokenised line runs git with one of these subcommands."""
    at_command_start = True
    for i, tok in enumerate(tokens):
        if tok in SHELL_SEPARATORS:
            at_command_start = True
            continue
        if at_command_start and tok.rsplit("/", 1)[-1] == "git":
            if _subcommand_after_git(tokens, i) in subcommands:
                return True
        at_command_start = _starts_a_command(tok, at_command_start)
    return False


def _trigger_matches(trigger: dict, tool_name: str, tool_input: dict) -> bool:
    t_tool = trigger.get("tool_name", "")
    # tool_names lets one rule cover Edit, Write and MultiEdit instead of three
    # copies that then drift apart. A hook that predates the field ignores it
    # and sees tool_name "", which matches nothing, so such a rule is inert
    # rather than universal on an older install. That is the safe direction.
    names = trigger.get("tool_names")
    names = names if isinstance(names, list) else []
    if t_tool != "*" and t_tool != tool_name and tool_name not in names:
        return False
    cm = trigger.get("command_match")
    # Absent means "match the tool whatever the command is"; an empty dict
    # means a matcher with no type, which the schema rejects outright. Reading
    # both as absent made `"command_match": {}` match every call to the tool.
    if cm is None:
        return True
    if tool_name != "Bash":
        return False
    return _command_matches(cm, str(tool_input.get("command", "")))


def _command_matches(cm: dict, command: str) -> bool:
    """One command_match sub-matcher against a Bash command."""
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


def _transcript_entries_reversed(transcript_path: str):
    """Parsed transcript entries, newest first.

    A line that is blank, unparseable, or not an object is skipped rather than
    ending the walk, because one corrupt line must not hide the apology above
    it. A file that cannot be read yields nothing, which allows the call.
    """
    if not transcript_path:
        return
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except OSError:
        return
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            yield entry


def _is_real_user_prompt(entry: dict) -> bool:
    """True for a prompt the person sent, False for a tool result.

    Tool results are recorded as type=="user" entries whose content is a list of
    tool_result blocks. Treating those as the turn boundary lets any tool call,
    even a denied one, clear the apology from view and reopen the gate. Only
    string content, or a list containing a text block, ends the current turn.
    """
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "text" for b in content)


def _assistant_blocks(entry: dict) -> list:
    """The content blocks of one assistant entry, oldest first."""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _scan_assistant_entry(entry: dict, answered: bool) -> tuple:
    """Search one assistant message for an apology, newest block first.

    Blocks are walked in reverse within the entry as well as across entries,
    because a message can apologize in a text block and call ``add_lesson`` in a
    later block of the same message. Returns ``(pattern, sentence, answered)``,
    where an empty pattern means keep walking.
    """
    for block in reversed(_assistant_blocks(entry)):
        btype = block.get("type")
        if btype == "tool_use" and block.get("name") == ADD_LESSON_TOOL:
            answered = True
        elif btype == "text":
            pattern, sentence = _apology_match(block.get("text") or "")
            if pattern:
                return pattern, sentence, answered
    return "", "", answered


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

    Falls back to no match on any read or parse error, which allows the call.
    Enforcement here is a net rather than a tripwire.
    """
    answered = False
    for entry in _transcript_entries_reversed(transcript_path):
        etype = entry.get("type")
        if etype == "user":
            if _is_real_user_prompt(entry):
                break
            continue
        if etype != "assistant":
            continue
        pattern, sentence, answered = _scan_assistant_entry(entry, answered)
        if pattern:
            return pattern, sentence, answered
    return "", "", False


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------





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
    except Exception:  # mgcp: allow-broad-except an unreadable pattern is not evidence the path matches
        return False


class EvalContext:
    """Everything a precondition may need, read at most once each.

    This exists because the alternative was one more positional parameter on
    ``_evaluate_precondition`` for every new input. It reached six that way and
    42 cyclomatic complexity, and the four types added here would have made it
    ten. The existing parameters stay, because ten callers pass them
    positionally; new inputs arrive through this object instead.

    Every accessor is lazy and caches, including failures, so a rule that needs
    the staged diff costs one subprocess for the whole tool call and a rule
    that needs nothing costs none. Each returns an empty value on any error,
    because this hook allows a call it cannot measure.
    """

    EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

    def __init__(self, hook_input=None, seed=None):
        """Built from the hook payload. ``seed`` pre-fills the cache.

        Two parameters rather than one per field, because this object exists
        to stop a parameter list from growing. ``seed`` is how a direct caller,
        such as a test, supplies a value without a repository.
        """
        payload = hook_input or {}
        self.tool_name = payload.get("tool_name", "")
        self.tool_input = payload.get("tool_input") or {}
        self.session_id = payload.get("session_id", "")
        self._transcript_path = payload.get("transcript_path", "")
        # Every command runs with -C the repository root, and every path is
        # made relative to it. Rules compare globs like "tests/**" against
        # paths printed relative to the root, so running from a subdirectory
        # would silently stop matching.
        self._root = None
        self._raw_dir = payload.get("cwd") or os.environ.get(
            "CLAUDE_PROJECT_DIR") or "."
        self._cache = dict(seed or {})

    @property
    def root(self):
        if self._root is None:
            rc, out = self._run(["rev-parse", "--show-toplevel"], self._raw_dir)
            self._root = out.strip() if rc == 0 and out.strip() else ""
        return self._root

    @staticmethod
    def _run(args, cwd):
        try:
            r = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                               text=True, timeout=10)
            return r.returncode, r.stdout
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    def git(self, args):
        """Run one read-only command at the repository root."""
        if not self.root:
            return 1, ""
        return self._run(args, self.root)

    def _cached(self, key, build):
        if key not in self._cache:
            try:
                self._cache[key] = build()
            except Exception:  # mgcp: allow-broad-except measurement cannot deny
                self._cache[key] = None
        return self._cache[key]

    @property
    def transcript_path(self):
        """The raw transcript path from the payload, "" when absent.

        The apology gate reads the file itself rather than this object's parsed
        ``transcript`` accessor, because it scans assistant text rather than
        tool uses.
        """
        return self._transcript_path

    def has_base(self):
        """True when HEAD is a commit with a non-empty tree.

        The ratchet's premise is that legacy code has a HEAD version to compare
        against. With no commit, or a commit whose tree is git's empty tree,
        every function reads as new and the gate would refuse a whole first
        import. It records that it skipped instead of refusing.
        """
        def build():
            rc, _ = self.git(["rev-parse", "--verify", "-q", "HEAD^{commit}"])
            if rc != 0:
                return False
            rc, out = self.git(["rev-parse", "HEAD^{tree}"])
            return rc == 0 and out.strip() != self.EMPTY_TREE
        return bool(self._cached("has_base", build))

    def staged_paths(self, added_only=False):
        """Staged paths relative to the root, never including deletions.

        Deletions are left out because a rule built on them refuses the commit
        that removes the offending file.
        """
        key = f"staged:{added_only}"

        def build():
            flt = "A" if added_only else "ACMR"
            rc, out = self.git(["diff", "--cached", "--name-only",
                                "--diff-filter=" + flt])
            return [x for x in out.splitlines() if x.strip()] if rc == 0 else []
        return self._cached(key, build) or []

    def staged_files(self):
        """Every staged path, deletions included.

        The coupling rule needs deletions, because removing a source file is
        still a change that may require a doc update.
        """
        def build():
            rc, out = self.git(["diff", "--cached", "--name-only"])
            return [x for x in out.splitlines() if x.strip()] if rc == 0 else []
        return self._cached("staged_files", build) or []

    def staged_diff(self):
        """The staged diff as text, capped so a large commit cannot stall."""
        def build():
            rc, out = self.git(["diff", "--cached", "--unified=0"])
            return out[:4_000_000] if rc == 0 else ""
        return self._cached("staged_diff", build) or ""

    def staged_numstat(self, exclude):
        """(added, removed, paths) for the staged set, skipping excluded paths.

        One parser for every rule that measures the size of a commit, so the
        budget and the message threshold can never disagree about how big it is.
        """
        def build():
            rc, out = self.git(["diff", "--cached", "--numstat"])
            if rc != 0:
                return (0, 0, [])
            added, removed, paths = 0, 0, []
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                plus, minus, path = parts
                # "-" marks a binary file, which has no line count.
                if not plus.isdigit() or not minus.isdigit():
                    continue
                if _path_excluded(path, exclude):
                    continue
                added, removed = added + int(plus), removed + int(minus)
                paths.append(path)
            return (added, removed, paths)
        key = f"numstat:{','.join(exclude or [])}"
        return self._cached(key, build) or (0, 0, [])

    def staged_added_lines(self, exclude):
        """Added lines across the staged set, skipping excluded paths."""
        return self.staged_numstat(exclude)[0]

    def blob(self, ref, path):
        """One path's content at a ref, or "" when it is not there."""
        def build():
            rc, out = self.git(["show", f"{ref}:{path}"])
            return out if rc == 0 else ""
        return self._cached(f"blob:{ref}:{path}", build) or ""

    def transcript(self):
        """Parsed transcript entries, oldest first. Empty on any read problem."""
        def build():
            if not self._transcript_path:
                return []
            try:
                with open(self._transcript_path) as fh:
                    lines = fh.readlines()
            except (OSError, IOError):
                return []
            out = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict):
                    out.append(entry)
            return out
        return self._cached("transcript", build) or []

    def tool_uses(self, names):
        """Every recorded tool_use block in this session whose name is wanted."""
        out = []
        for entry in self.transcript():
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") in names:
                    out.append(block)
        return out

    def message_text(self):
        """The commit message this call carries, or "".

        Read from the command, which is where the prose-style rule reads it, so
        two rules can never disagree about what the message is. Covers -m,
        --message, --message=, -F, --file, --file= and a heredoc body. A form
        this cannot read returns "", and the caller allows rather than guessing.
        """
        return self._cached("message", self._build_message) or ""

    def _build_message(self):
        command = str(self.tool_input.get("command", ""))
        if not command:
            return ""
        parts = self._message_flags(command)
        if parts:
            return "\n\n".join(parts)
        found = re.search(r"<<-?\s*['\"]?(\w+)['\"]?\n(.*?)\n\1", command, re.S)
        return found.group(2) if found else ""

    # Flags that carry the message inline, and flags that name a file holding it.
    _INLINE_FLAGS = ("-m", "--message")
    _FILE_FLAGS = ("-F", "--file")

    def _message_flags(self, command):
        """Message text from every flag form, in the order it appears."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        parts, i = [], 0
        while i < len(tokens):
            text, step = self._flag_value(tokens, i)
            if text:
                parts.append(text)
            i += step
        return parts

    def _flag_value(self, tokens, i):
        """(message text, how many tokens consumed) for the flag at i."""
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in self._INLINE_FLAGS:
            return nxt, 2 if nxt else 1
        if tok in self._FILE_FLAGS:
            return (self._read_file(nxt), 2) if nxt else ("", 1)
        if tok.startswith("--message="):
            return tok.split("=", 1)[1], 1
        if tok.startswith("--file="):
            return self._read_file(tok.split("=", 1)[1]), 1
        return "", 1

    def _read_file(self, path):
        if path == "-":
            return ""     # the message comes from stdin, which the hook cannot see
        for cand in (path, f"{self.root}/{path}" if self.root else path):
            try:
                with open(cand) as fh:
                    return fh.read()
            except (OSError, IOError):
                continue
        return ""


def _glob_match(path, pattern):
    """One glob against a repo-relative POSIX path, with working ``**``.

    fnmatch cannot express "zero or more directories": its ``*`` crosses a path
    separator, so ``src/**/*.py`` requires at least one directory under src and
    silently misses a module added directly there. That hole let a rule gated on
    ``src/**/*.py`` pass a new top-level module, which is the case the rule
    exists for. Both ``**`` forms are handled here instead.
    """
    try:
        if fnmatch.fnmatch(path, pattern):
            return True
        if "/**/" in pattern and fnmatch.fnmatch(
                path, pattern.replace("/**/", "/", 1)):
            return True
        if pattern.endswith("/**") and fnmatch.fnmatch(path, pattern[:-3] + "/*"):
            return True
    except Exception:  # mgcp: allow-broad-except a bad glob must never deny
        return False
    return False


def _path_excluded(path, globs):
    """True when a repo-relative path matches any glob."""
    return any(_glob_match(path, g) for g in globs or [])


def _h_diff_budget(pre, state, ctx):
    """Refuse a commit whose staged NET growth passes the budget.

    Net, added minus removed, because the complaint this gate answers is a
    codebase that only grows. Counting additions alone refuses the refactor
    that removes 800 lines and adds 320, which is the work the budget exists
    to encourage.

    Measured at the commit, not at the edit. A session total latches: once it
    passes the budget, every later edit is over it for the rest of the session,
    including the edits that delete code, so the only exit left is the bypass
    and the gate dies the first time someone uses it. The audit log recorded
    that state 14 times with the same frozen number. A commit is one decision
    with two real exits, stage less or say why, and it cannot strand a session.
    """
    if ctx is None or not ctx.root:
        return True, ""
    max_net = int(pre.get("max_net_lines") or 0)
    max_files = int(pre.get("max_files") or 0)
    if max_net <= 0 and max_files <= 0:
        return True, ""

    added, removed, paths = ctx.staged_numstat(pre.get("exclude_globs") or [])
    over = _budget_overruns(added - removed, len(paths), max_net, max_files)
    if not over:
        return True, ""
    return False, ("This commit is over budget:\n  " + "\n  ".join(over)
                   + f"\n  measured +{added} -{removed} over {len(paths)} files"
                   + "\n  files: " + ", ".join(sorted(paths)[:10]))


def _budget_overruns(net, files, max_net, max_files):
    """Each budget this commit exceeds, named with both numbers."""
    over = []
    if max_net > 0 and net > max_net:
        over.append(f"net lines {net:+d}, budget {max_net}")
    if max_files > 0 and files > max_files:
        over.append(f"files touched {files}, budget {max_files}")
    return over


def _h_staged_python_complexity(pre, state, ctx):
    """Refuse staged Python that worsens a function or adds one over a limit.

    The metric module is imported HERE, not at module level. At module level an
    ImportError is raised before main's own handler exists, so the hook exits 1
    with empty output, which the harness reads as allow. One missing file would
    take every rule dark, including the git gate. Imported here, the same
    failure costs one inert gate and leaves a row in the audit log.
    """
    qm = _ratchet_ready(ctx)
    if qm is None:
        return True, ""

    limits = dict(qm.DEFAULT_LIMITS)
    limits.update(pre.get("limits") or {})
    exclude = pre.get("exclude_globs") or []
    checks = pre.get("banned") or []
    paths = [p for p in ctx.staged_paths()
             if p.endswith(".py") and not _path_excluded(p, exclude)]
    exempt = pre.get("file_length_exempt") or []

    violations = []
    for path in paths:
        violations.extend(_file_violations(
            qm, ctx, path, limits, checks, _path_excluded(path, exempt)))
    if not violations:
        return True, ""
    return False, qm.format_violations(violations)


def _ratchet_ready(ctx):
    """The metric module when the ratchet can run, else None.

    Three reasons it cannot: no repository, no metric module installed, or no
    HEAD commit with content to compare against. Each one allows the call, and
    the last two leave a row in the audit log so an inert gate is visible
    rather than looking like a clean result.
    """
    if ctx is None or not ctx.root:
        return None
    qm = _load_metrics()
    if qm is None:
        return None
    if not ctx.has_base():
        _audit({"event": "skipped_precondition",
                "type": "staged_python_complexity",
                "reason": "no HEAD commit with content to compare against"})
        return None
    return qm


def _load_metrics():
    """The metric module, or None when it is not installed.

    Imported here and not at module level. At module level an ImportError is
    raised before main's own handler exists, so the hook exits 1 with empty
    output, which the harness reads as allow: one missing file would take every
    rule dark, including the git gate. Here the same failure costs one inert
    gate and leaves a row in the audit log.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import quality_metrics
        return quality_metrics
    except Exception as exc:  # mgcp: allow-broad-except one gate, not all of them
        _audit({"event": "skipped_precondition",
                "type": "staged_python_complexity",
                "reason": f"quality_metrics unavailable: {exc!r}"[:200]})
        return None


def _file_violations(qm, ctx, path, limits, checks, skip_file_length=False):
    """Ratchet violations and banned patterns for one staged file."""
    # An empty ref means the index, so this reads the staged blob.
    staged_src = ctx.blob("", path)
    after = qm.measure_source(staged_src, path)
    if not after:
        return []
    head_src = ctx.blob("HEAD", path)
    before = qm.measure_source(head_src, path) if head_src else {}
    out = list(qm.compare(before, after, limits, skip_file_length))
    for b in qm.banned_patterns(staged_src, checks):
        out.append({
            "name": f"{path}:{b['lineno']}", "metric": b["pattern"],
            "before": None, "after": b["detail"], "limit": "banned",
            "lineno": b["lineno"], "reason": b["detail"],
        })
    return out


def _h_commit_message_requires(pre, state, ctx):
    """Require a pattern in the message once the change is big enough."""
    if ctx is None or not ctx.root:
        return True, ""
    pattern = pre.get("pattern") or ""
    if not pattern:
        return True, ""
    exclude = pre.get("exclude_globs") or []
    added = ctx.staged_added_lines(exclude)
    if added < int(pre.get("min_added_lines") or 0):
        return True, ""

    message = ctx.message_text()
    if not message:
        # A form the hook cannot read, such as --file=- or an editor session.
        # Allowing is the only honest answer. Guessing would refuse a commit
        # whose message is fine.
        _audit({"event": "skipped_precondition",
                "type": "commit_message_requires",
                "reason": "no message text in the command"})
        return True, ""
    if _matches_or_unusable(pattern, message):
        return True, ""
    return False, (f"{added} added lines, and the message does not match "
                   f"{pattern!r}.")


def _matches_or_unusable(pattern, text):
    """True when the pattern matches, or when the pattern itself is broken.

    A bad regex in the rules file must not refuse every commit.
    """
    try:
        return bool(re.search(pattern, text))
    except re.error:
        return True


def _h_transcript_tool_called(pre, state, ctx):
    """Require a tool call with matching input somewhere in this session.

    Read from the transcript rather than from turn_tools_called, for the reason
    the apology gate learned in v2.16. That list lives in a file shared by
    every project and every concurrent session, and the agent being gated can
    write it. The transcript is per session and it cannot.
    """
    want = pre.get("tool_name", "")
    if not _precondition_has_work(ctx, want, pre.get("when_staged_added", [])):
        return True, ""
    if not ctx.transcript():
        # No readable transcript means the question cannot be answered, not
        # that the answer is no. Refusing here would block every commit that
        # adds a module whenever transcript_path is absent, which is the
        # opposite of a net. An empty result is recorded so an inert gate is
        # visible rather than looking like a clean pass.
        _audit({"event": "skipped_precondition", "type": "transcript_tool_called",
                "reason": "no readable transcript for this session"})
        return True, ""
    wanted = pre.get("input_match", {})
    if any(_input_matches(b.get("input") or {}, wanted)
           for b in ctx.tool_uses((want,))):
        return True, ""
    return False, _missing_call_detail(want, wanted)


def _precondition_has_work(ctx, want, gate_globs):
    """True when this precondition has something to check.

    Named for the question it answers. It was called _rule_applies, which reads
    like "is this rule enabled and triggered", and a later helper took that name
    for that other question. Python allowed the redefinition silently, the
    handler here called the wrong function, and the broad except turned the
    resulting TypeError into a precondition that passed. Two tests caught it.
    """
    if ctx is None or not want:
        return False
    return _gate_applies(ctx, gate_globs or [])


def _missing_call_detail(want, wanted):
    """Why the rule refused, naming the call it looked for."""
    if wanted:
        return f"no {want} call in this session with {wanted}"
    return f"no {want} call in this session"


def _input_matches(recorded, wanted):
    """True when every wanted key matches the recorded tool input."""
    return all(str(recorded.get(k)) == str(v) for k, v in wanted.items())


def _gate_applies(ctx, gate_globs):
    """True when this rule applies to what is staged.

    No globs means the rule always applies. Otherwise it applies only when a
    file this commit ADDS matches one, which is the difference between adding a
    module and editing one.
    """
    if not gate_globs:
        return True
    if not ctx.root:
        return False
    return any(_path_excluded(p, gate_globs)
               for p in ctx.staged_paths(added_only=True))


def _h_tool_called_this_turn(pre, state, ctx):
    name = pre.get("tool_name", "")
    if name in (state.get("turn_tools_called") or []):
        return True, ""
    return False, f"Required tool not called this turn: {name}"


def _h_tool_not_called_this_turn(pre, state, ctx):
    name = pre.get("tool_name", "")
    if name not in (state.get("turn_tools_called") or []):
        return True, ""
    return False, f"Forbidden tool called this turn: {name}"


def _h_staged_files_coupling(pre, state, ctx):
    unsatisfied = []
    for c in pre.get("couplings") or []:
        when = c.get("when_staged") or []
        req = c.get("require_one_of") or []
        if not when or not req:
            continue
        ok, triggering = _check_coupling(ctx.staged_files(), when, req)
        if not ok:
            unsatisfied.append(
                f"  - staged: {', '.join(triggering)} -> require one of: {', '.join(req)}"
            )
    if not unsatisfied:
        return True, ""
    return False, "Doc-coupling violations:\n" + "\n".join(unsatisfied)


def _h_staged_files_forbid(pre, state, ctx):
    deny_globs = pre.get("deny_globs") or []
    if not deny_globs:
        return True, ""
    hits = [p for p in ctx.staged_paths()
            if any(_safe_fnmatch(p, g) for g in deny_globs)]
    if not hits:
        return True, ""
    return False, ("staged files are forbidden by this rule:\n"
                   + "\n".join(f"  - {h}" for h in hits[:20]))


def _h_staged_content_forbid(pre, state, ctx):
    patterns = pre.get("patterns") or []
    added = _added_lines(ctx.staged_diff())
    if not patterns or not added:
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
    return False, ("staged content matches a forbidden pattern:\n"
                   + "\n".join(f"  - {m!r} matched {p!r}" for p, m in hits[:10]))


def _h_tool_input_glob(pre, state, ctx):
    field = pre.get("field", "")
    deny_globs = pre.get("deny_globs") or []
    value = (ctx.tool_input or {}).get(field)
    if not field or not deny_globs or not isinstance(value, str):
        return True, ""
    for pattern in deny_globs:
        if _safe_fnmatch(value, pattern):
            return False, (f"tool_input.{field} = {value!r} matches deny "
                           f"pattern {pattern!r}")
    return True, ""


# Every precondition type has exactly one handler, looked up here. This was an
# if-chain that reached 42 cyclomatic complexity on six types, and each new
# type made the whole function harder to read while adding a parameter to it.
# A dict means the dispatcher stays at 3 whatever gets added, and each rule's
# logic is readable on its own.
_HANDLERS = {
    "tool_called_this_turn": _h_tool_called_this_turn,
    "tool_not_called_this_turn": _h_tool_not_called_this_turn,
    "staged_files_coupling": _h_staged_files_coupling,
    "staged_files_forbid": _h_staged_files_forbid,
    "staged_content_forbid": _h_staged_content_forbid,
    "tool_input_glob": _h_tool_input_glob,
    "diff_budget": _h_diff_budget,
    "staged_python_complexity": _h_staged_python_complexity,
    "commit_message_requires": _h_commit_message_requires,
    "transcript_tool_called": _h_transcript_tool_called,
}



def _evaluate_precondition(
    pre: dict, state: dict, staged_files: list = None, tool_input: dict = None,
    ctx: "EvalContext" = None,
):
    """Dispatch one precondition to its handler.

    Three parameters are the ones callers already pass positionally, so every
    existing call site and test keeps working. When no context is given, one is
    built with those values seeded into its cache, which is how a test
    exercises a rule without a repository.

    The staged diff and the staged path list used to be parameters here too.
    They are git reads, so they belong on the context with every other git
    read, and moving them stopped this list at five. Growing it by one per
    input is how it reached six parameters and 42 cyclomatic complexity.
    """
    handler = _HANDLERS.get((pre or {}).get("type", ""))
    if handler is None:
        # An unknown type fails open, on the record. A mistyped type disables
        # the whole rule silently, and the schema that would have rejected it
        # never sees a hand-edited rules file.
        _audit({"event": "skipped_precondition",
                "type": (pre or {}).get("type", "")})
        return True, ""
    if ctx is None:
        staged = list(staged_files or [])
        ctx = EvalContext(
            {"tool_input": tool_input or {}},
            # A direct caller has no repository, so seed the git reads it would
            # otherwise make. "staged:False" is the cache key staged_paths uses.
            seed={"staged_files": staged, "staged:False": staged},
        )
    return handler(pre, state, ctx)


# ---------------------------------------------------------------------------
# Config + state
# ---------------------------------------------------------------------------


# Rules a shipped change replaced, skipped by name on load.
#
# The rules file is a copy under ~/.mgcp that only a write tool refreshes, so a
# retired rule sits there until something rewrites it. edit-diff-budget fired
# on Edit and read a field this hook no longer has, which would leave its file
# budget comparing the staged file count at edit time. Skipping it here is what
# makes a `git pull` safe before the next `mgcp-init`.
RETIRED_RULES = ("edit-diff-budget",)


def _load_rules() -> list:
    """Load enforcement rules. Returns [] on any failure (fail open)."""
    try:
        if not ENFORCEMENT_CONFIG.exists():
            return []
        with open(ENFORCEMENT_CONFIG) as f:
            data = json.load(f)
        rules = data.get("rules") or []
        if not isinstance(rules, list):
            return []
        return [r for r in rules
                if not (isinstance(r, dict) and r.get("name") in RETIRED_RULES)]
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
        if not STATE_FILE.exists():
            return {}
        with open(STATE_FILE) as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    for key in ("turn_tools_called", "turn_bypass_scopes"):
        if key in loaded and not isinstance(loaded[key], list):
            loaded[key] = []
    return loaded


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


def _adjudication_applies(state, session_id):
    """True when this session contested the gate and the verdict stands.

    An adjudication speaks only for the session that recorded it, because
    workflow_state.json is shared across concurrent sessions and an unscoped
    verdict would open every one of them.

    Every read is defensive. This file is agent-writable, and a crash here
    means exit 1 with empty stdout, which the harness reads as ALLOW, so a
    malformed value must read as "no adjudication" and keep the gate shut.
    """
    adjudication = state.get("turn_apology_adjudication")
    if not isinstance(adjudication, dict):
        return False
    if adjudication.get("verdict") != "not_apology":
        return False
    recorded = adjudication.get("session_id")
    if not isinstance(recorded, str):
        recorded = ""
    # Exact match only. Treating "" as "applies to everyone" is reachable by
    # type confusion from a forged session_id. The single-session case still
    # works, because both sides are then "".
    return recorded == (session_id if isinstance(session_id, str) else "")


def _record_apology_compliance(ctx, state):
    """Note on the record that an apology was answered with a lesson.

    A compliance is a label too: it says this fire was accepted. Without the
    sentence it would be an unexaminable vote.
    """
    if ADD_LESSON_TOOL in (state.get("turn_tools_called") or []):
        return
    matched_pattern, flagged_sentence, answered = _latest_apology(ctx.transcript_path)
    if matched_pattern and not answered:
        _audit({"event": "comply", "gate": "apology",
                "session_id": ctx.session_id,
                "pattern": matched_pattern,
                "flagged_sentence": flagged_sentence,
                "lesson_id": (ctx.tool_input or {}).get("id", "")})


def _deny_unless_apology_answered(ctx, state):
    """Refuse this tool call while an apology in this turn is unanswered."""
    if _adjudication_applies(state, ctx.session_id):
        return
    matched_pattern, flagged_sentence, answered = _latest_apology(ctx.transcript_path)
    if not matched_pattern or answered:
        return
    _deny([
        "[apology-requires-add-lesson] You apologized in this "
        "turn. Two exits: (1) COMPLY -- call "
        "mcp__mgcp__add_lesson capturing what you should do "
        "differently next time; or (2) CONTEST -- call "
        "mcp__mgcp__adjudicate_apology_gate with the flagged "
        "text, a verdict and your reasoning, which goes on the "
        "audit record. Bypass: include MGCP_BYPASS:apology in "
        "the next user prompt."
    ], audit={"gate": "apology", "tool_denied": ctx.tool_name,
              "session_id": ctx.session_id,
              "pattern": matched_pattern,
              "flagged_sentence": flagged_sentence})


def _apology_gate(ctx, state, bypass_scopes):
    """The gate whose trigger is assistant text rather than a tool argument.

    While it is armed, the only permitted calls are add_lesson, the
    adjudication tool and the three discovery tools. Gating discovery would
    gate the exits themselves. It runs independently of
    enforcement_rules.json, because a data rule matches on tool arguments and
    this one reads what the assistant said.

    It enforces the rule that an apology triggers a knowledge write
    immediately, promoted from a passive note to a refusal.
    """
    if APOLOGY_BYPASS_SCOPE in bypass_scopes:
        return
    if ctx.tool_name == ADD_LESSON_TOOL:
        _record_apology_compliance(ctx, state)
        return
    if ctx.tool_name == ADJUDICATE_TOOL or ctx.tool_name in DISCOVERY_TOOLS:
        return
    _deny_unless_apology_answered(ctx, state)


def _rule_matches_call(rule, ctx, bypass_scopes):
    """Whether this rule should be checked against this tool call.

    A malformed rule reads as not applying, because enforcement is a net rather
    than a tripwire and one bad entry must not take the others down.
    """
    try:
        if not rule.get("enabled", True):
            return False
        if rule.get("bypass_scope", "") in bypass_scopes:
            return False
        return _trigger_matches(rule.get("trigger") or {}, ctx.tool_name,
                                ctx.tool_input)
    except Exception:  # mgcp: allow-broad-except a malformed rule must not take the other rules down
        return False


def _unsatisfied_preconditions(rule, state, ctx):
    """The detail text of every precondition this call fails.

    A precondition that raises counts as satisfied. This hook allows a call it
    cannot measure.
    """
    unsatisfied = []
    for pre in rule.get("preconditions") or []:
        try:
            ok, detail = _evaluate_precondition(pre or {}, state, ctx=ctx)
        except Exception:
            ok, detail = True, ""
        if not ok:
            unsatisfied.append(detail)
    return unsatisfied


def _rule_denials(rules, state, ctx, bypass_scopes):
    """One deny message per rule this tool call violates.

    A rule in audit mode records what it would have refused and contributes no
    message. Every new rule ships that way and is promoted on evidence, so a
    limit that is wrong costs a log row rather than a blocked session.
    would_deny is counted separately from deny in the dashboard, because a
    projection is not an action.
    """
    denials = []
    for rule in rules:
        if not _rule_matches_call(rule, ctx, bypass_scopes):
            continue
        unsatisfied = _unsatisfied_preconditions(rule, state, ctx)
        if not unsatisfied:
            continue
        name = rule.get("name", "?")
        details = "\n".join(unsatisfied)
        if rule.get("mode") == "audit":
            _audit({"event": "would_deny", "gate": "rules",
                    "tool_denied": ctx.tool_name, "session_id": ctx.session_id,
                    "rules": [name], "detail": details[:1000]})
            continue
        reason = rule.get("deny_reason") or f"Rule '{name}' violated"
        denials.append(f"[{name}] {reason}\n{details}")
    return denials


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _allow()

    rules = _load_rules()
    state = _load_state()
    bypass_scopes = set(state.get("turn_bypass_scopes") or [])
    if BYPASS_ALL in bypass_scopes:
        _allow()

    # One context for the whole tool call. Every accessor on it is lazy and
    # caches, so a rule that needs the staged diff costs one subprocess and a
    # rule that needs nothing costs none. This replaced three separate lazy
    # variables that each had to be threaded through the evaluator.
    ctx = EvalContext(hook_input)

    _apology_gate(ctx, state, bypass_scopes)

    if not rules:
        _allow()

    denials = _rule_denials(rules, state, ctx, bypass_scopes)
    if denials:
        _deny(denials, audit={
            "gate": "rules", "tool_denied": ctx.tool_name,
            "session_id": ctx.session_id,
            "rules": [d.split("]")[0].lstrip("[") for d in denials]})
    _allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never let a crash silently allow a tool call
        _fail_open(exc)
