#!/usr/bin/env python3
"""
SessionStart hook for MGCP (Memory Graph Control Protocol).
Injects context telling Claude to load lessons and project context.
"""
import json
import os

project_path = os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd())

context = f"""## Session Startup Instructions

You have access to the MGCP (Memory Graph Control Protocol) MCP server.

### At Session Start (DO THIS NOW):
1. Call `mcp__mgcp__get_project_context` with project_path: "{project_path}"
2. Call `mcp__mgcp__query_lessons` with a task_description based on what the user is asking about

### During the Session - Use MGCP Tools at These Triggers:

**When you make an architectural decision:**
→ `mcp__mgcp__add_catalogue_decision` (title, decision, rationale, alternatives)

**When you discover files that change together:**
→ `mcp__mgcp__add_catalogue_coupling` (files, reason)

**When you find a gotcha, quirk, or important pattern:**
→ `mcp__mgcp__add_catalogue_arch_note` (title, description, category)

**When you notice a security concern:**
→ `mcp__mgcp__add_catalogue_security_note` (title, description, severity)

**When you establish or notice a coding convention:**
→ `mcp__mgcp__add_catalogue_convention` (title, rule, category)

**When you learn something reusable for ANY future session:**
→ `mcp__mgcp__add_lesson` (id, trigger, action, rationale)

### Before Session End or Committing:
ALWAYS call `mcp__mgcp__save_project_context` with:
- `notes`: Summary of what was accomplished
- `active_files`: Key files you worked on
- `decision`: Any major decisions made (optional)

### Trigger Phrases to Watch For:
- "let's commit" / "commit this" → save_project_context FIRST
- "shutting down" / "end session" / "done for now" → save_project_context
- "decided to" / "chose X over Y" → add_catalogue_decision
- "these files are related" / "coupled" → add_catalogue_coupling
- "watch out for" / "gotcha" / "quirk" → add_catalogue_arch_note
"""

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context
    }
}

print(json.dumps(output))