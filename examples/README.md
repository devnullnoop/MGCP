# MGCP Examples

This directory contains example configurations for integrating MGCP with Claude Code.

## Claude Code Hooks

The shipped hook templates live in `src/mgcp/hook_templates/`. That is the single copy, and the one
`mgcp-init` installs from. The `claude-hooks/` directory here holds only the `settings.json`
registration reference.

### Available Hooks

| Hook | Event | Type | Purpose |
|------|-------|------|---------|
| `session-init.py` | SessionStart | advisory | Load project context, inject the session-start bootstrap checklist |
| `user-prompt-dispatcher.py` | UserPromptSubmit | advisory | Keyword gates and intent routing from `~/.mgcp/intent_config.json`, scheduled reminders, per-turn enforcement state reset |
| `pre-tool-dispatcher.py` | PreToolUse | **enforcing** | Evaluates `~/.mgcp/enforcement_rules.json` on every tool call and denies tools whose preconditions are unsatisfied |
| `post-tool-dispatcher.py` | PostToolUse | advisory | Knowledge-capture checkpoint after Edit/Write, error detection after Bash, per-turn tool tracking |
| `mgcp-precompact.py` | PreCompact | advisory | Critical reminder to save context before compression |

Three single-purpose regex hooks (`git-reminder.py`, `catalogue-reminder.py`, `task-start-reminder.py`) came before these five and are superseded by them. They are in git history, not in this directory.

### Setup

The easiest way is to use `mgcp-init`:

```bash
mgcp-init --client claude-code
```

This automatically configures the MCP server and creates project hooks.

For manual setup, copy the hooks from `src/mgcp/hook_templates/` in the MGCP checkout to your project and
register them as shown in `claude-hooks/settings.json`.

Note: The `mgcp-init` command is strongly recommended as it generates the hooks with correct paths.

### How Hooks Work

**SessionStart** (`session-init.py`):
- Fires when a new session starts
- Injects the bootstrap checklist (read_soliloquy / get_project_context / query_lessons)
- Detects stale hook references in settings.json and overdue REM operations

**UserPromptSubmit** (`user-prompt-dispatcher.py`):
- Fires when the user sends any message
- Applies hard keyword gates and intent routing loaded from `~/.mgcp/intent_config.json`
- Delivers scheduled reminders and resets per-turn enforcement state
- This is the key to making lessons **proactive** rather than passive

**PreToolUse** (`pre-tool-dispatcher.py`) — the only *enforcing* hook:
- Fires before every tool call
- Evaluates the data-driven rules in `~/.mgcp/enforcement_rules.json` and returns a deny decision when preconditions are unsatisfied — the harness then refuses to run the tool
- Without this entry in settings.json there is **no enforcement at all**, only advisory reminders

**PostToolUse** (`post-tool-dispatcher.py`):
- Fires after every tool call
- Edit/Write triggers a knowledge-capture checkpoint; Bash triggers error detection
- Records each tool name for PreToolUse `tool_called_this_turn` preconditions

**PreCompact** (`mgcp-precompact.py`):
- Fires before context window compression
- Critical warning to save context and write a soliloquy before context is lost

## MCP Server Configuration

Example configuration for `~/.config/claude-code/settings.json`:

```json
{
  "mcpServers": {
    "mgcp": {
      "command": "python",
      "args": ["-m", "mgcp.server"]
    }
  }
}
```

Or using the installed command:

```json
{
  "mcpServers": {
    "mgcp": {
      "command": "/path/to/venv/bin/mgcp"
    }
  }
}
```

## Writing Custom Hooks

UserPromptSubmit hooks are powerful for keyword detection:

```python
#!/usr/bin/env python3
import json
import re
import sys

hook_input = json.load(sys.stdin)
prompt = hook_input.get("prompt", "").lower()

# Detect keywords
if re.search(r"\bdeploy\b", prompt):
    print("<reminder>Check deployment checklist before deploying</reminder>")

sys.exit(0)
```

Register in `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/my-hook.py"
          }
        ]
      }
    ]
  }
}
```