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

from pydantic import BaseModel, ConfigDict, Field

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
    """Match specifier for a tool call.

    ``extra="forbid"`` is load-bearing. Every enforcement MCP tool rewrites the
    whole file from these models, including ``toggle_enforcement_rule``, so a
    field the model does not know is silently dropped on the next write. That
    turns a one-field typo into deleted enforcement, discovered only when the
    rule stops firing. An unknown field now fails as loudly as an unknown
    ``type`` already does.
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str  # exact match; "*" = any tool
    # One rule for several tools, instead of three copies of the same rule.
    # Matches when tool_name matches OR the called tool is in this list.
    tool_names: list[str] = Field(default_factory=list)
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
    - ``staged_files_forbid`` — deny if any staged file path matches a
      glob in ``deny_globs``. Unlike ``staged_files_coupling`` this asks
      for nothing in return; the path itself is refused. Used to keep a
      class of file out of the history, such as regenerable research
      output.
    - ``staged_content_forbid`` — deny if the staged diff matches any
      regular expression in ``patterns``. This is the only type that
      reads content rather than paths, which is what a private name or an
      absolute home directory needs: both arrive inside a sentence, not as
      a filename. Keep the patterns in the rules file rather than in the
      repository, so a list of private names is never itself published.
      An unreadable diff or an invalid pattern is skipped, because
      enforcement is a net rather than a tripwire.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "tool_called_this_turn",
        "tool_not_called_this_turn",
        "staged_files_coupling",
        "tool_input_glob",
        "staged_files_forbid",
        "staged_content_forbid",
        "diff_budget",
        "staged_python_complexity",
        "commit_message_requires",
        "transcript_tool_called",
    ]
    tool_name: str = ""
    couplings: list[dict] = Field(default_factory=list)
    field: str = ""
    deny_globs: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    # Shared by the three types that measure a change.
    exclude_globs: list[str] = Field(default_factory=list)
    # diff_budget
    max_net_lines: int = 0
    max_files: int = 0
    # staged_python_complexity
    limits: dict = Field(default_factory=dict)
    banned: list[str] = Field(default_factory=list)
    # Files exempt from the FILE LENGTH row only, so their functions are still
    # measured. A whole-file exclusion would exempt the project's largest and
    # most complex files from every metric, which is the opposite of what the
    # gate is for.
    file_length_exempt: list[str] = Field(default_factory=list)
    # commit_message_requires
    min_added_lines: int = 0
    pattern: str = ""
    # transcript_tool_called
    input_match: dict = Field(default_factory=dict)
    when_staged_added: list[str] = Field(default_factory=list)


class EnforcementRule(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    # "audit" records a would_deny row in gate_audit.jsonl and ALLOWS the call.
    # Every new rule ships in audit mode and is promoted on evidence.
    #
    # `enabled` remains the real kill switch, and a new rule ships disabled as
    # well. `mode` is a key no previously deployed hook has heard of, and an
    # unknown key there is neither a parse error nor a fail-open: it is ignored
    # and the rule enforces. That is the exact state after a git pull and
    # before `mgcp-init` installs the mode-aware hook, so audit mode alone
    # would have enforced on a machine that had only pulled.
    mode: Literal["enforce", "audit"] = "enforce"
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
            "change documented behavior, so update README in the same commit "
            "rather than as a follow-up. If this is a no-op version bump with no "
            "user-visible change, bypass with MGCP_BYPASS:docs."
        ),
    ),
    EnforcementRule(
        name="commit-message-prose-style",
        description=(
            "Refuse a commit whose message contains an em dash. Prose committed "
            "to a repository follows ASD-STE100 and the Google developer "
            "documentation style guide, and the em dash used as a pause is the "
            "clearest sign that a sentence was not written to either. This backs "
            "the human-prose-no-ai-tells lesson with a rule, because that lesson "
            "was written on 2026-08-26 after the same complaint and did not hold "
            "on its own. The check is mechanical and narrow on purpose. One "
            "character, no guessing. The rest of the style is the author's job, "
            "and the lesson states it."
        ),
        enabled=True,
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(
                type="git_subcommand",
                subcommands=["commit"],
            ),
        ),
        preconditions=[
            Precondition(
                type="tool_input_glob",
                field="command",
                # The message reaches git inside the Bash command, whether it
                # arrives through -m or a heredoc, so the command string is where
                # it can be read.
                deny_globs=["*\u2014*"],
            ),
        ],
        bypass_scope="prose",
        deny_reason=(
            "This commit message contains an em dash. Rewrite the sentence with "
            "a period, a comma, or a colon. Prose in this repository follows "
            "ASD-STE100 and the Google developer documentation style guide: one "
            "idea per sentence, 20 words or fewer, active voice, and every term "
            "defined where it first appears. Call "
            "query_lessons('commit message prose style') for the full rule. If "
            "the message quotes an em dash on purpose, for example when "
            "describing this rule, bypass with MGCP_BYPASS:prose."
        ),
    ),
    EnforcementRule(
        name="rem-required-before-commit",
        description=(
            "Force REM cycle execution before git commit/push. REM has no "
            "auto-trigger; without forced execution at commit time the "
            "schedule drifts unboundedly. Seeded DISABLED by default, because fresh "
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


# ----------------------------------------------------------------------------
# Structured coding gates. A new one ships DISABLED and in audit mode, and is
# promoted once its own audit rows say the limit is right.
# commit-complexity-ratchet is promoted, and ships enabled and enforcing.
#
# A rule still in audit carries two switches, not one. `mode="audit"` is
# honoured only by a hook that knows the key, and a hook is a COPY under
# ~/.mgcp/hooks that `git pull` does not refresh. So the state right after a
# pull is a new rule read by an old hook, which ignores `mode` and enforces.
# `enabled=False` is the one switch every deployed hook has always honoured, so
# it is what makes the pull safe. Turn a rule on after `mgcp-init` installs the
# mode-aware hook, and after audit data says the limits are right.
# ----------------------------------------------------------------------------
STRUCTURE_RULES: list[EnforcementRule] = [
    EnforcementRule(
        name="commit-diff-budget",
        description=(
            "Refuse a commit whose staged source grows by more than 300 net "
            "lines, or touches more than 12 files. Net means added minus "
            "removed, so a refactor that cuts 800 lines and adds 320 passes. "
            "It replaced edit-diff-budget, which counted a session's additions "
            "and latched: past the budget, every later edit was over it for the "
            "rest of the session, deletions included."
        ),
        enabled=False,
        mode="audit",
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(type="git_subcommand", subcommands=["commit"]),
        ),
        preconditions=[
            Precondition(
                type="diff_budget",
                max_net_lines=300,
                max_files=12,
                exclude_globs=[
                    "tests/**", "docs/**", "*.md", "**/bootstrap_data/**",
                    # Regenerated, not written. One SBOM refresh is 3,543 added
                    # lines, which would refuse the commit and swamp the trend
                    # the dashboard draws from the same measurement.
                    "sbom.cdx.json",
                ],
            ),
        ],
        bypass_scope="size",
        deny_reason=(
            "This commit grows the source by more than its net budget.\n"
            "Two exits: stage the part that is already coherent and commit "
            "that, or state in the message why the growth is the smallest way "
            "to get the result.\n"
            "Deletions count in your favour, so removing the code this change "
            "makes redundant is the cheapest exit of the three.\n"
            "Bypass with MGCP_BYPASS:size in your next prompt."
        ),
    ),
    EnforcementRule(
        name="commit-complexity-ratchet",
        description=(
            "Refuse a commit whose staged Python makes a function worse on a "
            "metric where it is already over the limit, or adds a function that "
            "starts over one. Legacy code does not block work; it only stops "
            "getting worse. 58 of 463 functions here are already over CC 10, so "
            "an absolute limit would refuse every commit."
        ),
        # Promoted from audit on 2026-10-09. Its audit rows held three findings
        # and no false ones: one new function at cyclomatic 12, and two broad
        # excepts that discarded the error. All three are fixed, so the gate
        # now holds a line the code already meets.
        enabled=True,
        mode="enforce",
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(type="git_subcommand", subcommands=["commit"]),
        ),
        preconditions=[
            Precondition(
                type="staged_python_complexity",
                limits={
                    "cyclomatic": 10, "length": 80, "depth": 4,
                    "params": 6, "file_lines": 1000,
                },
                banned=["swallowed_error", "pass_through_wrapper"],
                # server.py is 3,399 lines, so the file-length row would refuse
                # every new MCP tool. Excluding it keeps the per-function
                # metrics there while dropping the file row. Tests are excluded
                # because four test files already pass 1,000 lines, including
                # the one every new case for this gate goes into.
                exclude_globs=[
                    "tests/**", "docs/**", "*.md", "**/bootstrap_data/**",
                ],
                # Five of 34 source files are already over the 1,000-line
                # limit, against a median of 363, and each is the place its
                # code belongs: every MCP tool, every installer path, every
                # dashboard route, every store method, every gate. The
                # file-length row would refuse growth in all five, which means
                # refusing to add an MCP tool. They are exempt from THAT ROW
                # ONLY, so every function in them is still measured. Splitting
                # them is an open decision in update_plan.md, and it is not a
                # decision this gate should force at commit time.
                file_length_exempt=[
                    "src/mgcp/server.py",
                    "src/mgcp/init_project.py",
                    "src/mgcp/web_server.py",
                    "src/mgcp/persistence.py",
                    "src/mgcp/hook_templates/pre-tool-dispatcher.py",
                ],
            ),
        ],
        bypass_scope="complexity",
        deny_reason=(
            "Staged Python makes a function worse, or adds one over a limit.\n"
            "Extract a helper or flatten the nesting, then retry.\n"
            "Bypass with MGCP_BYPASS:complexity in your next prompt."
        ),
    ),
    EnforcementRule(
        name="commit-requires-why",
        description=(
            "A commit over 40 added lines carries a Why: paragraph, so the "
            "reason travels with git blame. The pattern allows the paragraph to "
            "wrap, because requiring 40 characters on one physical line refuses "
            "text wrapped at the width this project writes to."
        ),
        enabled=False,
        mode="audit",
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(type="git_subcommand", subcommands=["commit"]),
        ),
        preconditions=[
            Precondition(
                type="commit_message_requires",
                min_added_lines=40,
                pattern=r"(?ms)^Why:\s+\S.{39,}",
                exclude_globs=[
                    "tests/**", "docs/**", "*.md", "**/bootstrap_data/**",
                ],
            ),
        ],
        bypass_scope="intent",
        deny_reason=(
            "A commit over 40 added lines needs a Why: paragraph.\n"
            "\n"
            "<subject, 50 characters or fewer>\n"
            "\n"
            "What: <the change in one or two sentences>\n"
            "Why: <the problem it solves or the constraint it meets>\n"
            "Rejected: <alternatives considered, and why not>\n"
            "\n"
            "Bypass with MGCP_BYPASS:intent in your next prompt."
        ),
    ),
    EnforcementRule(
        name="new-module-requires-decision",
        description=(
            "A commit that ADDS a Python module or changes pyproject.toml is "
            "preceded by a catalogue decision in the same session. The commit "
            "message serves git blame; the catalogue entry is what "
            "search_catalogue returns to the next session before it changes the "
            "same code, which is where intent is actually lost."
        ),
        enabled=False,
        mode="audit",
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(type="git_subcommand", subcommands=["commit"]),
        ),
        preconditions=[
            Precondition(
                type="transcript_tool_called",
                tool_name="mcp__mgcp__add_catalogue_item",
                input_match={"item_type": "decision"},
                when_staged_added=["src/**/*.py", "pyproject.toml"],
            ),
        ],
        bypass_scope="intent",
        deny_reason=(
            "A new module or dependency is staged with no catalogue decision "
            "this session.\n"
            "Record one with add_catalogue_item(item_type='decision'), naming "
            "the alternatives you rejected.\n"
            "Bypass with MGCP_BYPASS:intent in your next prompt."
        ),
    ),
    EnforcementRule(
        name="refactor-commits-keep-tests",
        description=(
            "A commit whose subject starts with Refactor: may not stage test "
            "changes. A refactor that needs its tests edited changed behaviour, "
            "so it was not a refactor. Uses only precondition types that "
            "already existed."
        ),
        enabled=False,
        mode="audit",
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(
                type="regex",
                pattern=r"\bgit\b.*\bcommit\b[\s\S]*Refactor:",
            ),
        ),
        preconditions=[
            Precondition(type="staged_files_forbid", deny_globs=["tests/**"]),
        ],
        bypass_scope="refactor",
        deny_reason=(
            "A Refactor: commit has staged test changes.\n"
            "If the tests had to change, the behaviour changed, so this is not a "
            "refactor. Split it, or retitle the commit.\n"
            "Bypass with MGCP_BYPASS:refactor in your next prompt."
        ),
    ),
    EnforcementRule(
        name="dependency-change-requires-sbom",
        description=(
            "A commit that changes pyproject.toml also stages sbom.cdx.json. A "
            "software bill of materials that drifts from the project is worse "
            "than none, because it answers the question wrongly and nothing "
            "says so. The coupling is the only moment the two can be kept "
            "together cheaply. Scanning for vulnerabilities is NOT done here: "
            "it needs network access, takes tens of seconds, and its answer "
            "changes on the advisory database's schedule rather than yours, so "
            "it belongs in scheduled CI."
        ),
        enabled=False,
        mode="audit",
        trigger=Trigger(
            tool_name="Bash",
            command_match=CommandMatch(
                type="git_subcommand",
                subcommands=["commit"],
            ),
        ),
        preconditions=[
            Precondition(
                type="staged_files_coupling",
                couplings=[{
                    "when_staged": ["pyproject.toml"],
                    "require_one_of": ["sbom.cdx.json"],
                }],
            ),
        ],
        bypass_scope="sbom",
        deny_reason=(
            "This commit changes pyproject.toml without updating sbom.cdx.json.\n"
            "Regenerate it, then stage both together:\n"
            "  cyclonedx-py environment .venv --of JSON --output-reproducible \\\n"
            "    --pyproject pyproject.toml --mc-type application -o sbom.cdx.json\n"
            "If this commit changes no dependency, bypass with MGCP_BYPASS:sbom "
            "in your next prompt."
        ),
    ),
]


def default_config() -> EnforcementConfig:
    return EnforcementConfig(version=1, rules=list(DEFAULT_RULES) + list(STRUCTURE_RULES))


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


# Rules a shipped change replaced. They are dropped on load, because
# `merge_missing_defaults` adds by name and never removes, so nothing else can
# take one out of a file the operator already has. The PreToolUse hook holds
# the same list, because it reads the file raw and is the half that denies.
RETIRED_RULES = ("edit-diff-budget",)

# Precondition keys a shipped change removed. `save_config` writes every schema
# field, so a file saved before a field was removed carries that key on every
# precondition, and `extra="forbid"` rejects it. Dropping the key on load is
# what keeps that file parsing. Without this, deleting one field stops the
# whole file loading, which takes every enforcement MCP tool and the dashboard
# down with it.
DROPPED_FIELDS = ("max_added_lines",)


def _without_retired(data: dict) -> dict:
    """The config with retired rules and removed keys taken out."""
    rules = []
    for rule in data.get("rules") or []:
        if not isinstance(rule, dict):
            rules.append(rule)      # not ours to repair; let validation report it
            continue
        if rule.get("name") in RETIRED_RULES:
            continue
        for pre in rule.get("preconditions") or []:
            if isinstance(pre, dict):
                for key in DROPPED_FIELDS:
                    pre.pop(key, None)
        rules.append(rule)
    return {**data, "rules": rules}


def load_config(path: Path | None = None) -> EnforcementConfig:
    """Load enforcement rules.

    A missing file means a fresh install, so the defaults are the truth.
    A file that exists but does not parse is NOT: substituting the defaults
    there is how a write tool silently overwrites rules the hook is still
    enforcing from the very file it could not read. Let it raise; each MCP
    tool already turns the exception into a message the caller sees.

    Retired rules and removed fields are taken out before validation, so a file
    written by an older version still loads. That is a rename of nothing: the
    next save writes the cleaned shape back.
    """
    p = path or _config_path()
    if not p.exists():
        return default_config()
    data = json.loads(p.read_text())
    if not isinstance(data, dict):
        return EnforcementConfig.model_validate(data)
    return EnforcementConfig.model_validate(_without_retired(data))


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


def merge_missing_defaults(path: Path | None = None) -> dict:
    """Append shipped rules the file does not have, by name. Add only.

    This exists because nothing else delivers a new shipped rule to an install
    that already has a rules file. ``load_config`` returns the defaults only
    when the file is MISSING, and the one other writer outside the MCP tools
    seeds on first install behind ``if not path.exists()``. So an operator who
    has used MGCP for a week never receives a rule added after their install.
    ``mgcp-bootstrap`` was the plan's named channel for this and has no
    enforcement code at all.

    Add only, and never upsert. A populated file is a customised file: rules
    get renamed, disabled, retuned and added by hand, and a shipped rule of the
    same name may deliberately differ from its default. Overwriting one by name
    would silently revert that, which is the failure this function is shaped to
    avoid. A rule already present is left byte-identical and in place, and the
    order of existing rules never changes.

    Returns {"added": [names], "kept": n, "path": str}.
    """
    p = path or _config_path()
    config = load_config(p)
    present = {r.name for r in config.rules}
    shipped = list(DEFAULT_RULES) + list(STRUCTURE_RULES)

    added = [r.model_copy(deep=True) for r in shipped if r.name not in present]
    # A rule in audit mode refuses nothing, so enabling it is safe AS LONG AS
    # the installed hook knows what audit mode is. A hook that predates the key
    # ignores it and enforces, and `git pull` does not refresh the installed
    # hooks. So an audit rule is enabled only once the deployed payload matches
    # the package, and left disabled otherwise with the reason returned.
    #
    # This matters because a disabled rule produces no would_deny rows either,
    # and those rows are the whole basis for promoting a rule later. Shipping
    # disabled and never enabling would make the gates decoration.
    hook_current, hook_detail = _deployed_hook_is_current()
    enabled_now = []
    for rule in added:
        if rule.mode == "audit" and hook_current:
            rule.enabled = True
            enabled_now.append(rule.name)

    if added:
        config.rules = list(config.rules) + added
        save_config(config, p)
    return {
        "added": [r.name for r in added],
        "enabled": enabled_now,
        "hook_current": hook_current,
        "hook_detail": hook_detail,
        "kept": len(present),
        "path": str(p),
    }


def _deployed_hook_is_current() -> tuple[bool, str]:
    """(is the installed hook payload the one this package ships, why).

    The installed hooks are copies under ~/.mgcp/hooks that Claude Code runs by
    absolute path. Only ``mgcp-init`` rewrites them, so the package and the
    deployed payload drift apart on every pull.
    """
    try:
        from .init_project import HOOK_TEMPLATES_DIR, VERSION_MARKER

        shipped = (HOOK_TEMPLATES_DIR / "VERSION").read_text().strip()
        marker = Path.home() / ".mgcp" / "hooks" / VERSION_MARKER
        if not marker.exists():
            return False, "no hooks are installed yet"
        installed = marker.read_text().strip()
        if installed != shipped:
            return False, (f"installed hook payload is {installed} and this "
                           f"package ships {shipped}")
        return True, f"installed hook payload {installed} matches the package"
    except Exception as exc:
        return False, f"could not read the installed hook payload: {exc}"


