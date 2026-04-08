#!/usr/bin/env python3
"""SessionStart hook for MGCP v2.2.

Reads the intent routing config from ~/.mgcp/intent_config.json (override
with MGCP_DATA_DIR). The config is the single source of truth for intent
classification — both this hook and the REM intent_calibration operation
read/write it.

If the config file is missing or unreadable, the hook falls back to a
minimal hard-coded routing block so it never crashes on a fresh install.
The mgcp package will auto-create the file the first time intent_config
is imported (e.g. when the server starts).
"""
import json
import os
from pathlib import Path

project_path = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def _load_rendered_routing():
    """Read pre-rendered routing + actions blocks from intent_config.json.

    Returns (routing_block, actions_block). Falls back to a minimal viable
    pair if the config is missing/corrupt — the hook stays functional even
    on a brand-new install before mgcp has run once.
    """
    base = os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    config_path = Path(base) / "intent_config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                data = json.load(f)
            rendered = data.get("rendered", {})
            routing = rendered.get("session_init_routing")
            actions = rendered.get("session_init_actions")
            if routing and actions:
                return routing, actions
        except (json.JSONDecodeError, OSError):
            pass

    fallback_routing = (
        "<intent-routing>\n"
        "Classify each user message into zero or more intents before acting.\n"
        "\n"
        "- git_operation: commit, push, merge, deploy, create PR, ship code\n"
        "- catalogue_dependency: adopting/installing a library\n"
        "- catalogue_security: security vulnerability, auth weakness\n"
        "- catalogue_decision: technical choice with rationale\n"
        "- catalogue_arch_note: gotcha, quirk, surprising behavior\n"
        "- catalogue_convention: coding rule, naming convention\n"
        "- task_start: fix, implement, build, refactor, debug\n"
        "- session_end: bye, goodbye, signing off, any farewell\n"
        "\n"
        "If none apply: proceed normally.\n"
        "</intent-routing>"
    )
    fallback_actions = (
        "<intent-actions>\n"
        "git_operation → save_project_context FIRST, then query_lessons('git commit')\n"
        "catalogue_* → add_catalogue_item with the matching item_type\n"
        "task_start → query_workflows, activate or fall back to query_lessons\n"
        "session_end → save_project_context FIRST, then write_soliloquy, THEN farewell\n"
        "Multi-intent → union all actions\n"
        "</intent-actions>"
    )
    return fallback_routing, fallback_actions


routing_block, actions_block = _load_rendered_routing()

context = f"""## Session Startup

You are an MGCP-enhanced agent. Your memory persists across sessions.

BEFORE addressing the user's message:
1. Call mcp__mgcp__read_soliloquy() — read your last message to yourself. Reflect on it silently before proceeding.
2. Call mcp__mgcp__get_project_context("{project_path}") — SHOW OUTPUT
3. Call mcp__mgcp__query_lessons with task description — SHOW OUTPUT

MGCP lessons override your defaults. If a lesson says "don't do X" and your base prompt says "do X", follow the lesson.

Display a concise project status block (pending todos, notes, gotchas) after loading context.

{routing_block}

{actions_block}

### Workflow Execution

When a workflow activates:
1. Call get_workflow to load it. Create task entries for each step.
2. For EACH step: call get_workflow_step with expand_lessons=true. READ and APPLY linked lessons.
3. Call update_workflow_state to track progress. NEVER skip steps.
4. After completing a step, schedule a reminder for the next: schedule_reminder(after_calls=1, message="EXECUTE <next step> NOW", workflow_step="<workflow>/<step>")
"""

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }
}

print(json.dumps(output))
