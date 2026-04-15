"""Generic enforcement engine for the PreToolUse hook.

The PreToolUse hook in ``hook_templates/pre-tool-dispatcher.py`` is a
*generic evaluator*: it reads a list of rules from
``~/.mgcp/enforcement_rules.json`` and applies them to each tool call.
Adding a new enforcement rule means calling an MCP tool or editing the
JSON — never editing hook code. This module is the Pydantic schema +
load/save + evaluator that the MCP tools and tests use.

Design invariants:

- Rules are data. The hook template has no hard-coded rules.
- The hook is a stdlib-only Python script (no mgcp import) so it loads
  the JSON directly and runs its own evaluator copy. This module and
  the hook evaluator implement the same semantics; see
  ``tests/test_enforcement.py`` for the shared behavioral contract.
- Fails open. Any parse error in a rule, preconditon, or trigger skips
  the rule rather than blocking the tool call. Enforcement is a safety
  net, not a tripwire.
- Bypass is per-scope. Each rule names a ``bypass_scope`` (short string
  like ``"git"`` or ``"docs"``). The user's prompt may contain
  ``MGCP_BYPASS:<scope>`` to disable one scope, or bare ``MGCP_BYPASS``
  to disable all. UserPromptSubmit parses tokens and writes
  ``turn_bypass_scopes`` to workflow_state.json.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ENFORCEMENT_CONFIG_FILENAME = "enforcement_rules.json"
BYPASS_ALL = "*"

SHELL_SEPARATORS = {"&&", "||", "&", ";", ";;", "|", "(", ")", "{", "}"}


# ============================================================================
# Schema
# ============================================================================


class CommandMatch(BaseModel):
    """Optional sub-matcher for Bash tool_input.command.

    ``git_subcommand`` matches only when the command actually invokes
    ``git <subcommand>`` at a shell command boundary — not inside quoted
    args. ``regex`` and ``contains`` do raw string matching on the full
    command.
    """

    type: Literal["git_subcommand", "regex", "contains"]
    subcommands: list[str] = Field(default_factory=list)
    pattern: str = ""


class Trigger(BaseModel):
    """Match specifier for a tool call."""

    tool_name: str  # exact match; "*" = any tool
    command_match: CommandMatch | None = None


class Precondition(BaseModel):
    """One condition that must hold for a matched tool call to be allowed.

    Types:

    - ``tool_called_this_turn`` — state.turn_tools_called contains
      ``tool_name``.
    - ``tool_not_called_this_turn`` — state.turn_tools_called does NOT
      contain ``tool_name``.
    - ``staged_files_coupling`` — for each coupling in ``couplings``, if
      any staged file matches ``when_staged`` (glob) then at least one
      staged file must match ``require_one_of`` (glob).
    """

    type: Literal[
        "tool_called_this_turn",
        "tool_not_called_this_turn",
        "staged_files_coupling",
    ]
    tool_name: str = ""
    couplings: list[dict] = Field(default_factory=list)


class EnforcementRule(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    trigger: Trigger
    preconditions: list[Precondition]
    bypass_scope: str
    deny_reason: str


class EnforcementConfig(BaseModel):
    version: int = 1
    rules: list[EnforcementRule] = Field(default_factory=list)


# ============================================================================
# Defaults
# ============================================================================


DEFAULT_RULES: list[EnforcementRule] = [
    EnforcementRule(
        name="git-requires-query-lessons",
        description=(
            "Block git commit / git push unless mcp__mgcp__query_lessons "
            "has been called in the current turn. Addresses the repeated "
            "query-before-git-operations failure mode (v1 through v4)."
        ),
        enabled=True,
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(
                type="git_subcommand",
                subcommands=["commit", "push"],
            ),
        ),
        preconditions=[
            Precondition(
                type="tool_called_this_turn",
                tool_name="mcp__mgcp__query_lessons",
            ),
        ],
        bypass_scope="git",
        deny_reason=(
            "git commit/push requires a query_lessons call in this turn first.\n"
            "Call mcp__mgcp__query_lessons(task_description=\"git commit\") now, "
            "read the results, then retry.\n"
            "To bypass this gate only: include MGCP_BYPASS:git in your next prompt. "
            "To bypass all gates: bare MGCP_BYPASS."
        ),
    ),
]


def default_config() -> EnforcementConfig:
    return EnforcementConfig(version=1, rules=list(DEFAULT_RULES))


# ============================================================================
# Persistence
# ============================================================================


def _config_path() -> Path:
    base = os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    return Path(base) / ENFORCEMENT_CONFIG_FILENAME


def load_config(path: Path | None = None) -> EnforcementConfig:
    """Load enforcement rules. Returns a default config on any failure."""
    p = path or _config_path()
    if not p.exists():
        return default_config()
    try:
        data = json.loads(p.read_text())
        return EnforcementConfig.model_validate(data)
    except (json.JSONDecodeError, OSError, ValueError):
        return default_config()


def save_config(config: EnforcementConfig, path: Path | None = None) -> None:
    p = path or _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config.model_dump(), indent=2))


# ============================================================================
# Evaluator
# ============================================================================


def tokenize_command(command: str) -> list[str]:
    """Tokenize a shell command, keeping quoted strings intact and
    splitting shell operators into their own tokens. Raises ValueError
    on unterminated quotes."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def detect_git_subcommand(command: str, subcommands: list[str]) -> str | None:
    """Return the matched subcommand if ``command`` invokes
    ``git <sub>`` at a command boundary for any sub in ``subcommands``."""
    try:
        tokens = tokenize_command(command)
    except ValueError:
        return None

    at_command_start = True
    for i, tok in enumerate(tokens):
        if tok in SHELL_SEPARATORS:
            at_command_start = True
            continue
        if at_command_start and tok == "git":
            if i + 1 < len(tokens) and tokens[i + 1] in subcommands:
                return tokens[i + 1]
        at_command_start = False
    return None


def trigger_matches(trigger: Trigger, tool_name: str, tool_input: dict) -> bool:
    """True iff the trigger matches the incoming tool call."""
    if trigger.tool_name != "*" and trigger.tool_name != tool_name:
        return False

    cm = trigger.command_match
    if cm is None:
        return True

    # command_match only applies to Bash
    if tool_name != "Bash":
        return False

    command = str(tool_input.get("command", ""))
    if cm.type == "git_subcommand":
        return detect_git_subcommand(command, cm.subcommands) is not None
    if cm.type == "regex":
        try:
            return re.search(cm.pattern, command) is not None
        except re.error:
            return False
    if cm.type == "contains":
        return cm.pattern in command
    return False


def get_staged_files(cwd: str) -> list[str]:
    """Run ``git diff --cached --name-only`` and return the list. Empty
    list on any git / subprocess failure."""
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


def check_coupling(
    staged: list[str], when_staged: list[str], require_one_of: list[str]
) -> tuple[bool, list[str]]:
    """Return (satisfied, triggering_staged_files).

    A coupling is satisfied iff either (a) no staged file matches
    ``when_staged``, or (b) at least one staged file matches
    ``require_one_of``.
    """
    triggering = [p for p in staged if any(fnmatch.fnmatch(p, w) for w in when_staged)]
    if not triggering:
        return True, []
    for p in staged:
        if any(fnmatch.fnmatch(p, r) for r in require_one_of):
            return True, triggering
    return False, triggering


def evaluate_precondition(
    pre: Precondition,
    state: dict,
    staged_files: list[str],
) -> tuple[bool, str]:
    """Evaluate one precondition. Returns (satisfied, failure_detail)."""
    called = state.get("turn_tools_called") or []

    if pre.type == "tool_called_this_turn":
        if pre.tool_name in called:
            return True, ""
        return False, f"Required tool not called this turn: {pre.tool_name}"

    if pre.type == "tool_not_called_this_turn":
        if pre.tool_name not in called:
            return True, ""
        return False, f"Forbidden tool called this turn: {pre.tool_name}"

    if pre.type == "staged_files_coupling":
        unsatisfied: list[str] = []
        for c in pre.couplings:
            when = c.get("when_staged") or []
            req = c.get("require_one_of") or []
            if not when or not req:
                continue
            ok, triggering = check_coupling(staged_files, when, req)
            if not ok:
                unsatisfied.append(
                    f"  - staged: {', '.join(triggering)} → require one of: {', '.join(req)}"
                )
        if not unsatisfied:
            return True, ""
        return False, "Doc-coupling violations:\n" + "\n".join(unsatisfied)

    # Unknown type — fail open (skip, don't block)
    return True, ""


class Violation(BaseModel):
    rule_name: str
    bypass_scope: str
    deny_reason: str
    precondition_details: list[str]


def evaluate_rules(
    config: EnforcementConfig,
    tool_name: str,
    tool_input: dict,
    state: dict,
    project_dir: str,
    bypass_scopes: set[str] | None = None,
) -> list[Violation]:
    """Apply every enabled, triggered, non-bypassed rule. Return
    violations (rules whose preconditions were not satisfied)."""
    bypass_scopes = bypass_scopes or set()
    if BYPASS_ALL in bypass_scopes:
        return []

    violations: list[Violation] = []
    staged_files: list[str] | None = None  # lazy fetch

    for rule in config.rules:
        if not rule.enabled:
            continue
        if rule.bypass_scope in bypass_scopes:
            continue
        try:
            if not trigger_matches(rule.trigger, tool_name, tool_input):
                continue
        except Exception:
            continue  # fail open on malformed trigger

        # Fetch staged files lazily (only if any precondition needs them)
        needs_staged = any(
            p.type == "staged_files_coupling" for p in rule.preconditions
        )
        if needs_staged and staged_files is None:
            staged_files = get_staged_files(project_dir)

        unsatisfied: list[str] = []
        for pre in rule.preconditions:
            try:
                ok, detail = evaluate_precondition(pre, state, staged_files or [])
            except Exception:
                ok, detail = True, ""  # fail open on malformed precondition
            if not ok:
                unsatisfied.append(detail)

        if unsatisfied:
            violations.append(
                Violation(
                    rule_name=rule.name,
                    bypass_scope=rule.bypass_scope,
                    deny_reason=rule.deny_reason,
                    precondition_details=unsatisfied,
                )
            )

    return violations


# ============================================================================
# Bypass token parser (used by UserPromptSubmit hook)
# ============================================================================


BYPASS_TOKEN_RE = re.compile(r"MGCP_BYPASS(?::([A-Za-z0-9_-]+))?", re.IGNORECASE)


def parse_bypass_scopes(prompt: str) -> list[str]:
    """Extract bypass scopes from a user prompt.

    - Bare ``MGCP_BYPASS`` → ``"*"`` (all)
    - ``MGCP_BYPASS:git`` → ``"git"``
    - Multiple tokens allowed.
    """
    scopes: list[str] = []
    for match in BYPASS_TOKEN_RE.finditer(prompt):
        scope = match.group(1)
        scopes.append(scope if scope else BYPASS_ALL)
    return scopes
