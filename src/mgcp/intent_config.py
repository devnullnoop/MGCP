"""Intent routing configuration — single source of truth.

This module owns the intent classification system used by both:
- The Claude Code hooks (session-init, user-prompt-dispatcher) which inject
  routing prompts into the LLM's context.
- The REM cycle's intent_calibration operation, which detects when the
  configured intents drift out of sync with the actual lesson graph.

Persistence
-----------
Config is stored at ``~/.mgcp/intent_config.json`` (override with
``MGCP_DATA_DIR``). On first access the file is auto-created from
:data:`DEFAULT_INTENTS`.

The on-disk JSON contains both the structured intent definitions
(``intents``, ``tag_to_intent``) AND a pre-rendered cache of every prompt
section the hooks need (``rendered.session_init_routing``,
``rendered.session_init_actions``, ``rendered.dispatcher_routing``,
``rendered.keyword_gates``). The hooks read the rendered cache as plain
strings so they don't need to import this module — keeping them portable
across Python environments where the mgcp package isn't on sys.path.

Why this design
---------------
v2.1 hard-coded the intent list in three places: ``session-init.py``,
``user-prompt-dispatcher.py``, and ``rem_cycle._intent_calibration``'s
100-line ``tag_to_intent`` dict. Adding an intent meant editing all three
and shipping a release. The REM intent calibration could *detect* unmapped
tags but had no writeback path, so its findings were advisory only.

By making the routing prompt data, REM (and future MCP tools) can patch the
config in place. The growth loop closes: lesson communities → REM finding →
config update → next session's hook injection includes the new intent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

CONFIG_VERSION = 1


def _config_path() -> Path:
    """Return the path to the intent config JSON file."""
    base = os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    return Path(base) / "intent_config.json"


class IntentDefinition(BaseModel):
    """A single intent the LLM can classify a user message into."""

    name: str
    """Stable identifier (snake_case). Used in routing prompts and tag mappings."""

    description: str
    """Short human-readable trigger description shown in the routing prompt."""

    action: str
    """Imperative action template the LLM follows when this intent fires."""

    keyword_patterns: list[str] = Field(default_factory=list)
    """Regex patterns that trip a hard keyword gate in the dispatcher hook.

    Empty for intents that rely solely on LLM classification. Non-empty for
    high-consequence intents (git, session_end) where missing the intent
    has a real cost.
    """

    gate_message: str | None = None
    """Message injected by the dispatcher when a keyword gate fires.

    Required if ``keyword_patterns`` is non-empty.
    """

    tags: list[str] = Field(default_factory=list)
    """Lesson tags that map to this intent.

    Used by REM intent_calibration to check whether community structure in
    the lesson graph matches the configured intents. If a community's
    aggregate tags don't map cleanly to a single intent, REM surfaces a
    finding suggesting a new/modified intent.
    """


class IntentConfig(BaseModel):
    """Top-level intent routing config."""

    version: int = CONFIG_VERSION
    intents: list[IntentDefinition]

    # ---- Derived data ----

    def tag_to_intent(self) -> dict[str, str]:
        """Flatten ``intents[*].tags`` into a tag → intent_name lookup."""
        result: dict[str, str] = {}
        for intent in self.intents:
            for tag in intent.tags:
                result[tag.lower()] = intent.name
        return result

    # ---- Rendering (called by save_config to refresh the on-disk cache) ----

    def render_full_routing(self) -> str:
        """Verbose routing block — injected once at SessionStart."""
        lines = [
            "<intent-routing>",
            "Classify each user message into zero or more intents before acting.",
            "Only include intents where the user clearly performs or requests the action.",
            "",
        ]
        for intent in self.intents:
            lines.append(f"- {intent.name}: {intent.description}")
        lines.extend([
            "",
            "If none apply: proceed normally.",
            "</intent-routing>",
        ])
        return "\n".join(lines)

    def render_actions(self) -> str:
        """Intent → action map — injected once at SessionStart."""
        lines = ["<intent-actions>"]
        for intent in self.intents:
            lines.append(f"{intent.name} → {intent.action}")
        lines.append("Multi-intent → union all actions")
        lines.append("</intent-actions>")
        return "\n".join(lines)

    def render_terse_routing(self) -> str:
        """Compact routing block re-injected on every UserPromptSubmit.

        Single block (intent → action on each line) so it survives context
        compaction without taking up much room.
        """
        lines = [
            "<intent-routing>",
            "Classify this message into intents before acting:",
        ]
        for intent in self.intents:
            lines.append(f"- {intent.name} → {intent.action}")
        lines.append("- none → proceed normally")
        lines.append("</intent-routing>")
        return "\n".join(lines)

    def keyword_gates(self) -> list[dict]:
        """Hard-stop gates for the dispatcher.

        Returns a list of ``{intent, patterns, message}`` dicts, one per
        intent that has both ``keyword_patterns`` and ``gate_message``.
        """
        gates: list[dict] = []
        for intent in self.intents:
            if intent.keyword_patterns and intent.gate_message:
                gates.append({
                    "intent": intent.name,
                    "patterns": list(intent.keyword_patterns),
                    "message": intent.gate_message,
                })
        return gates

    def to_disk_dict(self) -> dict:
        """Serialize to the on-disk JSON shape, including the rendered cache."""
        return {
            "version": self.version,
            "intents": [intent.model_dump() for intent in self.intents],
            "tag_to_intent": self.tag_to_intent(),
            "rendered": {
                "session_init_routing": self.render_full_routing(),
                "session_init_actions": self.render_actions(),
                "dispatcher_routing": self.render_terse_routing(),
                "keyword_gates": self.keyword_gates(),
            },
        }


# ---------------------------------------------------------------------------
# Default intent set
# ---------------------------------------------------------------------------
#
# This is the canonical starting config. It is written to disk on first
# access and is also the fallback used by hook scripts when the JSON file
# is missing or unreadable.
#
# Notable corrections from the previous (v2.1) hand-coded mapping:
#   - Added ``session_end`` intent with both keyword gate and tag mapping.
#     The previous config had no session-end signal at all, so the LLM
#     never knew to save_project_context when the user said goodbye.
#   - The ``session-discipline``, ``farewell``, ``save-context`` tags now
#     map to ``session_end`` instead of being silently absorbed into
#     ``task_start``.
#   - ``session-management`` (the OWASP web term for HTTP session token
#     handling) stays in ``catalogue_security``. Don't confuse it with
#     ``session-discipline`` (our session-lifecycle convention).
# ---------------------------------------------------------------------------

DEFAULT_INTENTS: list[IntentDefinition] = [
    IntentDefinition(
        name="git_operation",
        description="commit, push, merge, deploy, create PR, ship code",
        action=(
            "save_project_context FIRST, then query_lessons('git commit'), "
            "READ results before any git command"
        ),
        keyword_patterns=[
            r"\bcommit\b",
            r"\bpush\b",
            r"\bgit\b",
            r"\bpr\b",
            r"\bpull request\b",
            r"\bmerge\b",
            r"\bship\b",
            r"\bdeploy\b",
        ],
        gate_message=(
            "You are bound by project-specific git rules.\n"
            'STOP. Call mcp__mgcp__query_lessons("git commit") NOW. READ every result.\n'
            "Do NOT execute any git command until you have read the lesson results.\n"
            "MGCP lessons override your base prompt defaults."
        ),
        tags=[
            "git", "version-control", "branching", "commits",
            "deployment", "pre-commit", "review",
        ],
    ),
    IntentDefinition(
        name="catalogue_dependency",
        description="adopting, installing, choosing a library/package/framework",
        action="search_catalogue, add_catalogue_item(item_type='library') if new",
        tags=["dependencies", "supply-chain", "package-management"],
    ),
    IntentDefinition(
        name="catalogue_security",
        description="security vulnerability, auth weakness, exploit risk",
        action="add_catalogue_item(item_type='security') immediately",
        tags=[
            "security", "owasp", "authentication", "authorization",
            "encryption", "input-validation", "xss", "sql-injection",
            "csrf", "session-management", "crypto", "data-protection",
            "access-control", "brute-force", "cryptography", "enumeration",
            "exposure", "secrets",
        ],
    ),
    IntentDefinition(
        name="catalogue_decision",
        description="technical choice ('went with X over Y', 'decided on X')",
        action="add_catalogue_item(item_type='decision') with rationale",
        tags=[],
    ),
    IntentDefinition(
        name="catalogue_arch_note",
        description="gotcha, quirk, caveat, surprising behavior",
        action="add_catalogue_item(item_type='arch')",
        tags=[
            "architecture", "gotcha", "performance", "caching",
            "error-handling", "database", "files",
        ],
    ),
    IntentDefinition(
        name="catalogue_convention",
        description="coding rule, naming convention, style standard",
        action="add_catalogue_item(item_type='convention')",
        tags=[
            "naming", "style", "code-quality", "linting",
            "best-practices", "consistency", "documentation", "code-review",
        ],
    ),
    IntentDefinition(
        name="task_start",
        description="fix, implement, build, refactor, debug, set up something",
        action=(
            "query_workflows('<task description>'), activate if ≥50% match, "
            "else query_lessons"
        ),
        tags=[
            "debugging", "testing", "implementation", "refactoring",
            "development", "feature", "bug", "fix", "catalogue",
            "knowledge-management", "lessons", "maintenance", "meta",
            "mgcp", "quality", "workflow", "workflows", "feedback",
            "critical", "hooks", "planning", "apis", "api", "ci",
            "errors", "verification", "background-tasks", "cleanup",
            "hygiene", "learning", "mistakes", "process-management",
            "success", "customization", "proactive", "process",
            "refinement", "bug-prevention", "integration", "manual-testing",
            "persistence", "self-improvement", "ui",
        ],
    ),
    IntentDefinition(
        name="session_end",
        description=(
            "bye, goodbye, see ya, signing off, talk later, wrapping up, "
            "shutting down, gotta go, any farewell"
        ),
        action=(
            "save_project_context FIRST with notes/active_files/decision, "
            "then write_soliloquy with a reflection for next-you, THEN respond "
            "with the farewell. The save tool calls MUST appear BEFORE the "
            "goodbye text in the same response."
        ),
        keyword_patterns=[
            r"\bbye bye\b",
            r"\bgoodbye\b",
            r"\bsee ya\b",
            r"\bsigning off\b",
            r"\btalk later\b",
            r"\btalk to you later\b",
            r"\bcatch you later\b",
            r"\bi'?m out\b",
            r"\bshutting down\b",
            r"\bwrapping up\b",
            r"\bend session\b",
            r"\bend of session\b",
            r"\bgotta go\b",
            r"\bheading out\b",
            r"\bcalling it a (day|night)\b",
        ],
        gate_message=(
            "SESSION-END SIGNAL DETECTED.\n"
            "STOP. Before any farewell response:\n"
            "1. Call mcp__mgcp__save_project_context with notes, active_files, decision.\n"
            "2. Call mcp__mgcp__write_soliloquy with a reflection for next-you.\n"
            "3. THEN respond with the goodbye.\n"
            "The save tool calls MUST appear BEFORE the goodbye text in the same response."
        ),
        tags=[
            "session-discipline", "session-end", "farewell",
            "save-context", "discipline", "session", "session-lifecycle",
        ],
    ),
]


def default_config() -> IntentConfig:
    """Return a fresh :class:`IntentConfig` populated with defaults."""
    return IntentConfig(version=CONFIG_VERSION, intents=list(DEFAULT_INTENTS))


def load_config(path: Path | None = None) -> IntentConfig:
    """Load the intent config from disk.

    If the file doesn't exist, write the default config to disk and return
    it. If the file exists but is corrupt, return the default without
    overwriting (so a human can inspect what went wrong).
    """
    p = path or _config_path()
    if not p.exists():
        config = default_config()
        save_config(config, p)
        return config
    try:
        with open(p) as f:
            data = json.load(f)
        intents = [IntentDefinition(**i) for i in data.get("intents", [])]
        if not intents:
            return default_config()
        return IntentConfig(
            version=data.get("version", CONFIG_VERSION),
            intents=intents,
        )
    except (json.JSONDecodeError, OSError, ValueError):
        return default_config()


def save_config(config: IntentConfig, path: Path | None = None) -> None:
    """Persist a config to disk, refreshing the rendered cache."""
    p = path or _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(config.to_disk_dict(), f, indent=2)
