"""Generic enforcement engine for the PreToolUse hook.

The PreToolUse hook in ``hook_templates/pre-tool-dispatcher.py`` is a
*generic evaluator*: it reads a list of rules from
``~/.mgcp/enforcement_rules.json`` and applies them to each tool call.
Adding a new enforcement rule means calling an MCP tool or editing the
JSON — never editing hook code. This module is the Pydantic schema +
load/save + evaluator that the MCP tools and tests use.

Scope of this module: the rule *schema* and its persistence, nothing more.

The evaluator lives in the hook and only in the hook. It must be
stdlib-only (the hook cannot import ``mgcp``), so a second copy here
would be a copy with no caller — which is what it was until it was
deleted. Read ``hook_templates/pre-tool-dispatcher.py`` for evaluation
semantics and ``tests/test_pre_tool_dispatcher.py`` for its contract;
``tests/test_enforcement.py`` covers this schema and its round-trip.

Design invariants:

- Rules are data. The hook template has no hard-coded rules.
- The hook fails open: any parse error in a rule, precondition, or
  trigger skips that rule rather than blocking the tool call.
  Enforcement is a safety net, not a tripwire.
- These tools fail loud instead. A rules file that exists but does not
  parse raises here, because the alternative is overwriting rules the
  hook is still enforcing.
- Bypass is per-scope. Each rule names a ``bypass_scope`` (short
  lowercase string like ``"git"`` or ``"docs"``). The user's prompt may
  contain ``MGCP_BYPASS:<scope>`` to disable one scope, or bare
  ``MGCP_BYPASS`` to disable all. UserPromptSubmit parses those tokens,
  lowercases the scope and writes ``turn_bypass_scopes`` to
  workflow_state.json; the hook compares it to ``bypass_scope`` verbatim,
  so an upper-case scope here can never be bypassed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ENFORCEMENT_CONFIG_FILENAME = "enforcement_rules.json"


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
    - ``tool_input_glob`` — inspect ``tool_input.<field>`` (a string)
      and deny if any glob in ``deny_globs`` matches it via fnmatch.
      Used to gate writes to sensitive paths (settings.json, secrets,
      etc.) by Edit/Write tools and to gate URL targets on web fetches.
      Fails open on missing field or non-string value.
    """

    type: Literal[
        "tool_called_this_turn",
        "tool_not_called_this_turn",
        "staged_files_coupling",
        "tool_input_glob",
    ]
    tool_name: str = ""
    couplings: list[dict] = Field(default_factory=list)
    field: str = ""
    deny_globs: list[str] = Field(default_factory=list)


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
    EnforcementRule(
        name="version-bump-requires-readme",
        description=(
            "Couple hook_templates/VERSION bumps to README.md updates in the "
            "same commit. Backs the publish-requires-doc-parity lesson with "
            "data enforcement so advisory knowledge alone doesn't have to "
            "carry the discipline. Triggered originally by v2.5 (3f4b621) "
            "shipping without README updates despite the v2.4 CHANGELOG "
            "literally citing this exact coupling as a use case."
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
                type="staged_files_coupling",
                couplings=[
                    {
                        "when_staged": ["src/mgcp/hook_templates/VERSION"],
                        "require_one_of": ["README.md"],
                    },
                ],
            ),
        ],
        bypass_scope="docs",
        deny_reason=(
            "A hook VERSION bump is staged without README.md. Version bumps "
            "change documented behavior — update README in the same commit, "
            "not as a follow-up. If this is a no-op version bump (no "
            "user-visible change), bypass with MGCP_BYPASS:docs."
        ),
    ),
    EnforcementRule(
        name="rem-required-before-commit",
        description=(
            "Force REM cycle execution before git commit/push. REM has no "
            "auto-trigger; without forced execution at commit time the "
            "schedule drifts unboundedly. Seeded DISABLED by default — fresh "
            "installs do not benefit from REM enforcement until they have "
            "lesson history and REM state. Toggle with "
            "toggle_enforcement_rule('rem-required-before-commit') once REM "
            "has run a few cycles. The SessionStart hook surfaces an "
            "advisory warning when operations are overdue regardless of "
            "this rule's enabled state."
        ),
        enabled=False,
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
                tool_name="mcp__mgcp__rem_run",
            ),
        ],
        bypass_scope="rem",
        deny_reason=(
            "git commit/push requires mcp__mgcp__rem_run in this turn first. "
            "REM cycles have no auto-trigger; this rule forces execution at "
            "commit time so the schedule does not drift unboundedly. Call "
            "mcp__mgcp__rem_run with no arguments to pick up every due "
            "operation, READ the findings, then retry the commit. To "
            "bypass this gate only: include MGCP_BYPASS:rem in your next "
            "prompt. To bypass all gates: bare MGCP_BYPASS."
        ),
    ),
]


def default_config() -> EnforcementConfig:
    return EnforcementConfig(version=1, rules=list(DEFAULT_RULES))


# ============================================================================
# Persistence
# ============================================================================


def _config_path() -> Path:
    # MGCP_ENFORCEMENT_CONFIG is honored by the PreToolUse hook; resolve it
    # here too so the MCP tools never edit a file the hook is not enforcing.
    override = os.environ.get("MGCP_ENFORCEMENT_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    return Path(base) / ENFORCEMENT_CONFIG_FILENAME


def load_config(path: Path | None = None) -> EnforcementConfig:
    """Load enforcement rules.

    A missing file means a fresh install, so the defaults are the truth.
    A file that exists but does not parse is NOT: substituting the defaults
    there is how a write tool silently overwrites rules the hook is still
    enforcing from the very file it could not read. Let it raise; each MCP
    tool already turns the exception into a message the caller sees.
    """
    p = path or _config_path()
    if not p.exists():
        return default_config()
    data = json.loads(p.read_text())
    return EnforcementConfig.model_validate(data)


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then os.replace.

    `open(p, "w")` truncates first, so a crash — or a second process reading
    mid-write — sees a half-written or empty config. os.replace is atomic on
    POSIX, so a reader sees either the whole old file or the whole new one.
    Cheap insurance on files the PreToolUse hook reads on every single call.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def save_config(config: EnforcementConfig, path: Path | None = None) -> None:
    _atomic_write(path or _config_path(), json.dumps(config.model_dump(), indent=2))


