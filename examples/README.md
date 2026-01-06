# MGCP Examples

This directory contains example configurations for integrating MGCP with Claude Code.

## Claude Code Hooks

The `claude-hooks/` directory contains templates for automatic session initialization.

### Setup

1. Copy the hooks to your project:
   ```bash
   mkdir -p your-project/.claude/hooks
   cp claude-hooks/session-init.py your-project/.claude/hooks/
   cp claude-hooks/settings.json your-project/.claude/
   ```

2. Edit `settings.json` to use your Python path if needed

3. Restart Claude Code in that project

### What the Hook Does

At session start, the hook injects instructions telling Claude to:
1. Load project context using `mcp__mgcp__get_project_context`
2. Query relevant lessons using `mcp__mgcp__query_lessons`

This ensures Claude has relevant knowledge before you start working.

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
