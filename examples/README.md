# MGCP Examples

This directory contains example configurations for integrating MGCP with Claude Code.

## Claude Code Hooks

The `claude-hooks/` directory contains templates for automatic session initialization and proactive lesson surfacing.

### Available Hooks

| Hook | Event | Purpose |
|------|-------|---------|
| `session-init.py` | SessionStart | Load project context, inject MGCP usage instructions |
| `git-reminder.py` | UserPromptSubmit | Detect "commit/push/git" keywords, remind to query lessons |
| `mgcp-reminder.sh` | PostToolUse (Edit/Write) | Remind to save lessons after code changes |
| `mgcp-precompact.sh` | PreCompact | Critical reminder to save before context compression |

### Setup

The easiest way is to use `mgcp-init`:

```bash
mgcp-init --client claude-code
```

This automatically configures the MCP server and creates project hooks.

For manual setup, copy hooks from the MGCP `.claude/hooks/` directory to your project.

Note: The `mgcp-init` command is strongly recommended as it generates the hooks with correct paths.

### How Hooks Work

**SessionStart** (`session-init.py`):
- Fires when a new session starts
- Injects instructions telling Claude to load project context and query lessons

**UserPromptSubmit** (`git-reminder.py`):
- Fires when user sends any message
- Checks for git-related keywords (commit, push, git, pr, merge)
- Injects reminder to query lessons before git operations
- This is the key to making lessons **proactive** rather than passive

**PostToolUse** (`mgcp-reminder.sh`):
- Fires after Edit/Write tool calls
- Short reminder to save lessons when learning something new

**PreCompact** (`mgcp-precompact.sh`):
- Fires before context window compression
- Critical warning to save all lessons before context is lost

## MCP Server Configuration

Example configuration for `~/.config/claude-code/settings.json`:

```json
{
  "mcpServers": {
    "mgcp": {
      "command": "python",
      "args": ["-m", "mgcp.server"],
      "cwd": "/path/to/MGCP"
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