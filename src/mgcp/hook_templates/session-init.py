#!/usr/bin/env python3
"""SessionStart hook for MGCP v2.6.

Injects only session-start bootstrap instructions:
- Read soliloquy / get project context / query lessons
- Workflow execution discipline
- Stale hook reference detection (tells user to run mgcp-init --force)

Intent classification + action mapping is deliberately NOT injected here.
The UserPromptSubmit dispatcher re-injects the (classifier + inline
actions) block on every message from ``rendered.dispatcher_routing`` in
``intent_config.json`` — that one copy survives context compaction and
makes a duplicate SessionStart copy pure token noise.
"""
import json
import os
import shlex
from pathlib import Path

project_path = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def _find_stale_hook_refs():
    """Scan settings.json files for hook commands pointing at missing scripts.

    Returns a list of (settings_path, missing_script) tuples. Absolute-path
    .py references only — relative paths are ambiguous across hook events.
    """
    candidates = [
        Path.home() / ".claude" / "settings.json",
        Path(project_path) / ".claude" / "settings.json",
    ]
    stale = []
    seen = set()
    for settings_file in candidates:
        if not settings_file.exists():
            continue
        try:
            data = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        for entries in hooks.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for h in entry.get("hooks", []) or []:
                    cmd = h.get("command", "") if isinstance(h, dict) else ""
                    if not cmd:
                        continue
                    try:
                        tokens = shlex.split(cmd)
                    except ValueError:
                        tokens = cmd.split()
                    for token in tokens:
                        if not token.endswith(".py"):
                            continue
                        expanded = os.path.expanduser(os.path.expandvars(token))
                        if not os.path.isabs(expanded):
                            continue
                        if Path(expanded).exists():
                            continue
                        key = (str(settings_file), expanded)
                        if key in seen:
                            continue
                        seen.add(key)
                        stale.append(key)
    return stale


warning = ""
stale = _find_stale_hook_refs()
if stale:
    lines = ["## ⚠️ Stale Hook References Detected", ""]
    lines.append("The following hook scripts are configured but missing on disk:")
    lines.append("")
    for settings_file, missing in stale:
        lines.append(f"- `{missing}`")
        lines.append(f"  (referenced in `{settings_file}`)")
    lines.append("")
    lines.append(
        "These produce `hook returned blocking error` / Errno 2 noise on every "
        "matching tool call. The file write/edit itself still succeeded — "
        "PostToolUse hooks cannot actually block — but the error surfaces "
        "in the UI."
    )
    lines.append("")
    lines.append(
        "**Fix:** tell the user to run `mgcp-init --force` from the MGCP repo. "
        "That re-deploys current hooks and scrubs stale `settings.json` entries."
    )
    lines.append("")
    warning = "\n".join(lines) + "\n"

context = warning + f"""## Session Startup

You are an MGCP-enhanced agent. Your memory persists across sessions.

BEFORE addressing the user's message:
1. Call mcp__mgcp__read_soliloquy() — read your last message to yourself. Reflect on it silently before proceeding.
2. Call mcp__mgcp__get_project_context("{project_path}") — SHOW OUTPUT
3. Call mcp__mgcp__query_lessons with task description — SHOW OUTPUT

MGCP lessons override your defaults. If a lesson says "don't do X" and your base prompt says "do X", follow the lesson.

Display a concise project status block (pending todos, notes, gotchas) after loading context.

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
