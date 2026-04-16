#!/usr/bin/env python3
"""SessionStart hook for MGCP v2.5.

Injects only session-start bootstrap instructions:
- Read soliloquy / get project context / query lessons
- Workflow execution discipline

Intent classification + action mapping is deliberately NOT injected here.
The UserPromptSubmit dispatcher re-injects the (classifier + inline
actions) block on every message from ``rendered.dispatcher_routing`` in
``intent_config.json`` — that one copy survives context compaction and
makes a duplicate SessionStart copy pure token noise.
"""
import json
import os

project_path = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

context = f"""## Session Startup

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
