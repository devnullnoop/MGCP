#!/usr/bin/env python3
"""
SessionStart hook for MGCP (Memory Graph Control Protocol).
Injects context telling Claude to load lessons and project context.

Copy this file to your project's .claude/hooks/ directory.
"""
import json
import os

project_path = os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd())

context = f"""## Session Startup Instructions

You have access to the MGCP (Memory Graph Control Protocol) MCP server. At the start of this session:

1. Call `mcp__mgcp__get_project_context` with project_path: "{project_path}"
2. Call `mcp__mgcp__query_lessons` with a task_description based on what the user is asking about

This ensures you have relevant lessons and project context loaded before proceeding.
"""

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context
    }
}

print(json.dumps(output))
