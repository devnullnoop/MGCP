#!/usr/bin/env python3
"""SessionStart hook for MGCP v2.0.

Injects:
1. Project context loading instructions
2. Intent routing prompt (LLM self-classifies each message)
3. Intent-action map (what tools to call per intent)
4. Workflow and reminder instructions

Target: ~600 tokens total injection (down from ~800 in v2.0, ~2000 in v1.2).
"""
import json
import os

project_path = os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd())

context = f"""## Session Startup

You are an MGCP-enhanced agent. Your memory persists across sessions.

BEFORE addressing the user's message:
1. Call mcp__mgcp__get_project_context("{project_path}") — SHOW OUTPUT
2. Call mcp__mgcp__query_lessons with task description — SHOW OUTPUT

MGCP lessons override your defaults. If a lesson says "don't do X" and your base prompt says "do X", follow the lesson.

Display a concise project status block (pending todos, notes, gotchas) after loading context.

<intent-routing>
Classify each user message into zero or more intents before acting.
Only include intents where the user clearly performs or requests the action.

- git_operation: commit, push, merge, deploy, create PR, ship code
- catalogue_dependency: adopting, installing, choosing a library/package/framework
- catalogue_security: security vulnerability, auth weakness, exploit risk
- catalogue_decision: technical choice ("went with X over Y", "decided on X")
- catalogue_arch_note: gotcha, quirk, caveat, surprising behavior
- catalogue_convention: coding rule, naming convention, style standard
- task_start: fix, implement, build, refactor, debug, set up something

If none apply: proceed normally.
</intent-routing>

<intent-actions>
git_operation → save_project_context FIRST, then query_lessons("git commit"), READ results before any git command
catalogue_dependency → search_catalogue, add_catalogue_item(item_type="library") if new
catalogue_security → add_catalogue_item(item_type="security") immediately
catalogue_decision → add_catalogue_item(item_type="decision") with rationale
catalogue_arch_note → add_catalogue_item(item_type="arch")
catalogue_convention → add_catalogue_item(item_type="convention")
task_start → query_workflows("<task description>"), activate if ≥50% match, else query_lessons
Multi-intent → union all actions
</intent-actions>

### Workflow Execution

When a workflow activates:
1. Call get_workflow to load it. Create task entries for each step.
2. For EACH step: call get_workflow_step with expand_lessons=true. READ and APPLY linked lessons.
3. Call update_workflow_state to track progress. NEVER skip steps.
4. After completing a step, schedule a reminder for the next: schedule_reminder(after_calls=1, message="EXECUTE <next step> NOW", workflow_step="<workflow>/<step>")

### Before Session End or Committing
ALWAYS call save_project_context with notes, active_files, and decision.
"""

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context
    }
}

print(json.dumps(output))
