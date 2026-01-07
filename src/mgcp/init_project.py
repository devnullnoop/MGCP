"""Initialize MGCP for any MCP-compatible LLM client."""

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def get_mgcp_python_path() -> str:
    """Get the path to the Python interpreter running MGCP."""
    return sys.executable


def get_mgcp_install_dir() -> Path:
    """Get the MGCP installation directory."""
    return Path(__file__).parent.parent.parent


def get_mcp_server_config() -> dict:
    """Get the standard MCP server configuration for MGCP."""
    return {
        "command": get_mgcp_python_path(),
        "args": ["-m", "mgcp.server"],
        "cwd": str(get_mgcp_install_dir())
    }


# ============================================================================
# LLM Client Definitions
# ============================================================================

@dataclass
class LLMClient:
    """Definition of an LLM client that supports MCP."""
    name: str
    display_name: str
    description: str
    get_config_path: Callable[[], Path]
    mcp_key: str  # Key in config for MCP servers (e.g., "mcpServers")
    config_wrapper: Callable[[dict], dict] | None = None  # Optional wrapper for the config


def _claude_code_path() -> Path:
    """
    Claude Code stores global MCP settings in ~/.claude/settings.json on all platforms.

    Note: Project-specific MCP configs are in ~/.claude.json under projects.<path>.mcpServers
    but those are managed separately via configure_claude_code_project().
    """
    return Path.home() / ".claude" / "settings.json"


def _cursor_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / ".cursor" / "mcp.json"
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Cursor" / "mcp.json"
    return Path.home() / ".cursor" / "mcp.json"


def _windsurf_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Codeium" / "windsurf" / "mcp_config.json"
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def _continue_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / ".continue" / "config.json"
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Continue" / "config.json"
    return Path.home() / ".continue" / "config.json"


def _cline_path() -> Path:
    """Cline stores MCP config in VS Code settings directory."""
    cline_subpath = Path("globalStorage") / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / cline_subpath
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Code" / "User" / cline_subpath
    return Path.home() / ".config" / "Code" / "User" / cline_subpath


def _zed_path() -> Path:
    """Zed editor stores settings in ~/.config/zed/settings.json"""
    if sys.platform == "darwin":
        return Path.home() / ".config" / "zed" / "settings.json"
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Zed" / "settings.json"
    return Path.home() / ".config" / "zed" / "settings.json"


def _claude_desktop_path() -> Path:
    """Claude Desktop app config location."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _cody_path() -> Path:
    """Sourcegraph Cody VS Code extension config."""
    cody_subpath = Path("globalStorage") / "sourcegraph.cody-ai" / "cody_mcp_settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / cody_subpath
    elif sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Code" / "User" / cody_subpath
    return Path.home() / ".config" / "Code" / "User" / cody_subpath


# Registry of supported LLM clients
LLM_CLIENTS: dict[str, LLMClient] = {
    "claude-code": LLMClient(
        name="claude-code",
        display_name="Claude Code",
        description="Anthropic's official CLI for Claude",
        get_config_path=_claude_code_path,
        mcp_key="mcpServers",
    ),
    "cursor": LLMClient(
        name="cursor",
        display_name="Cursor",
        description="AI-powered code editor",
        get_config_path=_cursor_path,
        mcp_key="mcpServers",
    ),
    "windsurf": LLMClient(
        name="windsurf",
        display_name="Windsurf",
        description="Codeium's AI code editor",
        get_config_path=_windsurf_path,
        mcp_key="mcpServers",
    ),
    "continue": LLMClient(
        name="continue",
        display_name="Continue",
        description="Open-source AI code assistant",
        get_config_path=_continue_path,
        mcp_key="experimental.modelContextProtocolServers",
    ),
    "cline": LLMClient(
        name="cline",
        display_name="Cline",
        description="AI assistant VS Code extension",
        get_config_path=_cline_path,
        mcp_key="mcpServers",
    ),
    "zed": LLMClient(
        name="zed",
        display_name="Zed",
        description="High-performance code editor with AI",
        get_config_path=_zed_path,
        mcp_key="context_servers",
    ),
    "claude-desktop": LLMClient(
        name="claude-desktop",
        display_name="Claude Desktop",
        description="Anthropic's desktop app for Claude",
        get_config_path=_claude_desktop_path,
        mcp_key="mcpServers",
    ),
    "cody": LLMClient(
        name="cody",
        display_name="Sourcegraph Cody",
        description="AI coding assistant by Sourcegraph",
        get_config_path=_cody_path,
        mcp_key="mcpServers",
    ),
}


# ============================================================================
# Claude Code Hooks (Claude Code specific feature)
# ============================================================================

HOOK_SCRIPT = '''#!/usr/bin/env python3
"""
SessionStart hook for MGCP (Memory Graph Control Protocol).
Injects context telling the LLM to load lessons and project context.
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
'''

REMINDER_HOOK_SCRIPT = '''#!/bin/bash
# PostToolUse hook - short reminder after code changes
echo "[MGCP] Did you learn something worth saving? Use mcp__mgcp__add_lesson"
'''

PRECOMPACT_HOOK_SCRIPT = '''#!/bin/bash
# PreCompact hook - CRITICAL reminder before context compression

cat << 'EOF'

╔════════════════════════════════════════════════════════════════════╗
║  CONTEXT COMPRESSION IMMINENT - SAVE YOUR LESSONS NOW              ║
╠════════════════════════════════════════════════════════════════════╣
║  Before this context compresses, you MUST:                         ║
║                                                                    ║
║  1. ADD any lessons learned this session:                          ║
║     → mcp__mgcp__add_lesson                                        ║
║                                                                    ║
║  2. SAVE project context for next session:                         ║
║     → mcp__mgcp__save_project_context                              ║
║                                                                    ║
║  If you learned ANYTHING worth remembering, add it NOW or lose it. ║
╚════════════════════════════════════════════════════════════════════╝

EOF
'''

GIT_REMINDER_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""UserPromptSubmit hook that detects git operations and reminds to query lessons."""
import json
import re
import sys

GIT_KEYWORDS = [r"\\bcommit\\b", r"\\bpush\\b", r"\\bgit\\b", r"\\bpr\\b", r"\\bpull request\\b", r"\\bmerge\\b"]

def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    prompt = hook_input.get("prompt", "").lower()

    for pattern in GIT_KEYWORDS:
        if re.search(pattern, prompt, re.IGNORECASE):
            print("""<user-prompt-submit-hook>
BEFORE executing any git operation (commit, push, PR):
1. Call mcp__mgcp__query_lessons with "git commit workflow"
2. Follow any project-specific git lessons (like attribution rules)
3. Then proceed with the git operation
</user-prompt-submit-hook>""")
            sys.exit(0)

    sys.exit(0)

if __name__ == "__main__":
    main()
'''

CATALOGUE_REMINDER_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""UserPromptSubmit hook that detects library/security/decision mentions."""
import json
import re
import sys

CATALOGUE_PATTERNS = [
    (r"\\b(using|chose|picked|selected|installed|added)\\b.{0,30}\\b(library|package|framework|tool)\\b", "dependency"),
    (r"\\b(pip install|npm install|cargo add|go get)\\b", "dependency"),
    (r"\\b(security|vulnerability|cve|exploit)\\b.{0,20}\\b(issue|bug|concern|risk)\\b", "security"),
    (r"\\b(decided|choosing|picked|went with)\\b.{0,20}\\b(over|instead of)\\b", "decision"),
    (r"\\b(gotcha|quirk|caveat|watch out|careful|tricky)\\b", "arch_note"),
    (r"\\b(convention|naming|style|always|never)\\b.{0,20}\\b(use|follow|do|avoid)\\b", "convention"),
]

REMINDERS = {
    "dependency": "mcp__mgcp__add_catalogue_dependency (name, purpose, version)",
    "security": "mcp__mgcp__add_catalogue_security_note (title, description, severity)",
    "decision": "mcp__mgcp__add_catalogue_decision (title, decision, rationale)",
    "arch_note": "mcp__mgcp__add_catalogue_arch_note (title, description, category)",
    "convention": "mcp__mgcp__add_catalogue_convention (title, rule, category)",
}

def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    prompt = hook_input.get("prompt", "")
    detected = set()

    for pattern, cat_type in CATALOGUE_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            detected.add(cat_type)

    if detected:
        print("<user-prompt-submit-hook>")
        print("Consider cataloguing: " + ", ".join(REMINDERS[t] for t in detected))
        print("</user-prompt-submit-hook>")

    sys.exit(0)

if __name__ == "__main__":
    main()
'''

HOOK_SETTINGS = {
    "hooks": {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/git-reminder.py"
                    },
                    {
                        "type": "command",
                        "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/catalogue-reminder.py"
                    }
                ]
            }
        ],
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/session-init.py"
                    }
                ]
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/mgcp-reminder.sh"
                    }
                ]
            }
        ],
        "PreCompact": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/mgcp-precompact.sh"
                    }
                ]
            }
        ]
    }
}


# ============================================================================
# Configuration Functions
# ============================================================================

def configure_client(client: LLMClient, dry_run: bool = False) -> dict:
    """
    Configure an LLM client with the MGCP MCP server.

    Args:
        client: The LLM client to configure
        dry_run: If True, don't write changes, just report what would happen

    Returns dict with status info.
    """
    result = {
        "client": client.display_name,
        "path": None,
        "status": None,
        "message": None,
    }

    settings_path = client.get_config_path()
    result["path"] = str(settings_path)

    mcp_config = get_mcp_server_config()

    # Load existing settings or create new
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            # Handle non-dict JSON (null, array, etc.)
            if not isinstance(settings, dict):
                result["status"] = "error"
                result["message"] = "Config file must contain a JSON object, not " + type(settings).__name__
                return result
        except json.JSONDecodeError:
            result["status"] = "error"
            result["message"] = "Could not parse existing config file"
            return result
    else:
        settings = {}
        if not dry_run:
            settings_path.parent.mkdir(parents=True, exist_ok=True)

    # Navigate to the correct key (handles nested keys like "experimental.modelContextProtocolServers")
    keys = client.mcp_key.split(".")
    current = settings
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]

    final_key = keys[-1]
    if final_key not in current or not isinstance(current[final_key], dict):
        current[final_key] = {}

    # Check if mgcp already configured
    if "mgcp" in current[final_key]:
        old_config = current[final_key]["mgcp"]
        if old_config != mcp_config:
            current[final_key]["mgcp"] = mcp_config
            if not dry_run:
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            result["status"] = "would_update" if dry_run else "updated"
            msg = "Would update MGCP server configuration" if dry_run else "Updated MGCP server configuration"
            result["message"] = msg
        else:
            result["status"] = "unchanged"
            result["message"] = "MGCP already configured"
    else:
        current[final_key]["mgcp"] = mcp_config
        if not dry_run:
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        result["status"] = "would_create" if dry_run else "created"
        result["message"] = "Would add MGCP server configuration" if dry_run else "Added MGCP server configuration"

    return result


def detect_installed_clients() -> list[str]:
    """Detect which LLM clients appear to be installed."""
    installed = []
    for name, client in LLM_CLIENTS.items():
        config_path = client.get_config_path()
        # Check if config exists OR if parent directory exists (client installed but not configured)
        if config_path.exists() or config_path.parent.exists():
            installed.append(name)
    return installed


def init_claude_hooks(project_dir: Path, dry_run: bool = False) -> dict:
    """
    Initialize Claude Code hooks in a project directory.

    Creates:
    - .claude/hooks/session-init.py       (SessionStart hook)
    - .claude/hooks/git-reminder.py       (UserPromptSubmit hook - git operations)
    - .claude/hooks/catalogue-reminder.py (UserPromptSubmit hook - catalogue prompts)
    - .claude/hooks/mgcp-reminder.sh      (PostToolUse hook)
    - .claude/hooks/mgcp-precompact.sh    (PreCompact hook)
    - .claude/settings.json               (Hook configuration)

    Args:
        project_dir: The project directory to initialize
        dry_run: If True, don't write changes, just report what would happen

    Returns dict with status info.
    """
    results = {
        "created": [],
        "would_create": [],
        "updated": [],
        "would_update": [],
        "skipped": [],
        "errors": [],
    }

    hooks_dir = project_dir / ".claude" / "hooks"
    settings_file = project_dir / ".claude" / "settings.json"

    # Define all hook files to create
    hook_files = [
        (hooks_dir / "session-init.py", HOOK_SCRIPT),
        (hooks_dir / "git-reminder.py", GIT_REMINDER_HOOK_SCRIPT),
        (hooks_dir / "catalogue-reminder.py", CATALOGUE_REMINDER_HOOK_SCRIPT),
        (hooks_dir / "mgcp-reminder.sh", REMINDER_HOOK_SCRIPT),
        (hooks_dir / "mgcp-precompact.sh", PRECOMPACT_HOOK_SCRIPT),
    ]

    # Check/create .claude/hooks directory
    if not dry_run:
        hooks_dir.mkdir(parents=True, exist_ok=True)

    # Write all hook files
    for hook_file, hook_content in hook_files:
        if hook_file.exists():
            results["skipped"].append(str(hook_file))
        else:
            if dry_run:
                results["would_create"].append(str(hook_file))
            else:
                hook_file.write_text(hook_content)
                hook_file.chmod(0o755)
                results["created"].append(str(hook_file))

    # Write project settings.json - MERGE hooks, don't replace
    if settings_file.exists():
        try:
            existing = json.loads(settings_file.read_text())
            if "hooks" not in existing:
                existing["hooks"] = {}

            # Merge MGCP hooks into existing hooks (preserves user's other hooks)
            needs_update = False
            for hook_type, hook_config in HOOK_SETTINGS["hooks"].items():
                if hook_type not in existing["hooks"]:
                    existing["hooks"][hook_type] = hook_config
                    needs_update = True
                # If hook type exists, check if MGCP hook is already there
                # Don't clobber existing hooks of the same type

            if needs_update:
                if dry_run:
                    results["would_update"].append(str(settings_file))
                else:
                    settings_file.write_text(json.dumps(existing, indent=2) + "\n")
                    results["updated"].append(str(settings_file))
            else:
                results["skipped"].append(str(settings_file))
        except json.JSONDecodeError:
            results["errors"].append(f"Could not parse existing {settings_file}")
    else:
        if dry_run:
            results["would_create"].append(str(settings_file))
        else:
            settings_file.write_text(json.dumps(HOOK_SETTINGS, indent=2) + "\n")
            results["created"].append(str(settings_file))

    return results


def configure_claude_code_project(project_path: str, dry_run: bool = False) -> dict:
    """
    Configure MGCP for a specific project in Claude Code's project-level config.

    Claude Code stores project-specific MCP servers in ~/.claude.json under
    projects.<path>.mcpServers. This is separate from the global config.

    Args:
        project_path: Absolute path to the project directory
        dry_run: If True, don't write changes

    Returns dict with status info.
    """
    result = {
        "project": project_path,
        "status": None,
        "message": None,
    }

    config_file = Path.home() / ".claude.json"
    mcp_config = get_mcp_server_config()

    if not config_file.exists():
        result["status"] = "skipped"
        result["message"] = "~/.claude.json not found (Claude Code not used yet?)"
        return result

    try:
        data = json.loads(config_file.read_text())
    except json.JSONDecodeError:
        result["status"] = "error"
        result["message"] = "Could not parse ~/.claude.json"
        return result

    # Ensure projects structure exists
    if "projects" not in data:
        data["projects"] = {}

    if project_path not in data["projects"]:
        data["projects"][project_path] = {}

    proj = data["projects"][project_path]
    if "mcpServers" not in proj:
        proj["mcpServers"] = {}

    # Check current state
    if "mgcp" in proj["mcpServers"]:
        if proj["mcpServers"]["mgcp"] == mcp_config:
            result["status"] = "unchanged"
            result["message"] = "MGCP already configured for this project"
        else:
            if not dry_run:
                proj["mcpServers"]["mgcp"] = mcp_config
                config_file.write_text(json.dumps(data))
            result["status"] = "would_update" if dry_run else "updated"
            result["message"] = "Would update project MGCP config" if dry_run else "Updated project MGCP config"
    else:
        if not dry_run:
            proj["mcpServers"]["mgcp"] = mcp_config
            config_file.write_text(json.dumps(data))
        result["status"] = "would_create" if dry_run else "created"
        result["message"] = "Would add MGCP to project" if dry_run else "Added MGCP to project"

    return result


def diagnose_claude_code() -> dict:
    """
    Diagnose Claude Code MGCP configuration issues.

    Checks both global and project-specific configs for problems.

    Returns dict with diagnostic results.
    """
    results = {
        "global_config": {"path": None, "status": None, "mgcp_configured": False},
        "project_configs": [],
        "issues": [],
        "suggestions": [],
    }

    # Check global config
    global_path = Path.home() / ".claude" / "settings.json"
    results["global_config"]["path"] = str(global_path)

    if global_path.exists():
        try:
            config = json.loads(global_path.read_text())
            results["global_config"]["status"] = "ok"
            if "mcpServers" in config and "mgcp" in config["mcpServers"]:
                results["global_config"]["mgcp_configured"] = True
                # Validate the config
                mgcp_cfg = config["mcpServers"]["mgcp"]
                if "command" in mgcp_cfg:
                    cmd = mgcp_cfg["command"]
                    if not Path(cmd).exists():
                        results["issues"].append(f"Global config: Python path does not exist: {cmd}")
                        results["suggestions"].append("Run 'mgcp-init --client claude-code' to fix")
        except json.JSONDecodeError:
            results["global_config"]["status"] = "parse_error"
            results["issues"].append("Could not parse ~/.claude/settings.json")
    else:
        results["global_config"]["status"] = "missing"

    # Check project configs in ~/.claude.json
    project_config_path = Path.home() / ".claude.json"
    if project_config_path.exists():
        try:
            data = json.loads(project_config_path.read_text())
            if "projects" in data:
                for proj_path, proj_data in data["projects"].items():
                    proj_info = {
                        "path": proj_path,
                        "has_mgcp": False,
                        "config": None,
                        "issues": [],
                    }

                    if "mcpServers" in proj_data:
                        servers = proj_data["mcpServers"]
                        if "mgcp" in servers:
                            proj_info["has_mgcp"] = True
                            proj_info["config"] = servers["mgcp"]

                            # Validate
                            if "command" in servers["mgcp"]:
                                cmd = servers["mgcp"]["command"]
                                if not Path(cmd).exists():
                                    proj_info["issues"].append(f"Python path does not exist: {cmd}")

                    results["project_configs"].append(proj_info)
        except json.JSONDecodeError:
            results["issues"].append("Could not parse ~/.claude.json")

    # Generate suggestions
    if not results["global_config"]["mgcp_configured"]:
        results["suggestions"].append("Run 'mgcp-init --client claude-code' to configure globally")

    return results


def verify_setup() -> dict:
    """
    Verify that MGCP is properly set up and can run.

    Returns dict with verification results.
    """
    import subprocess

    results = {
        "python_valid": False,
        "mgcp_importable": False,
        "server_starts": False,
        "clients_configured": [],
        "errors": [],
    }

    # Check Python path exists
    python_path = get_mgcp_python_path()
    if Path(python_path).exists():
        results["python_valid"] = True
    else:
        results["errors"].append(f"Python not found at: {python_path}")

    # Check MGCP can be imported
    try:
        import mgcp.server  # noqa: F401 - intentionally checking importability
        results["mgcp_importable"] = True
    except ImportError as e:
        results["errors"].append(f"Cannot import mgcp.server: {e}")

    # Check server can start (quick test)
    try:
        proc = subprocess.run(
            [python_path, "-c", "from mgcp.server import mcp; print('ok')"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(get_mgcp_install_dir())
        )
        if proc.returncode == 0 and "ok" in proc.stdout:
            results["server_starts"] = True
        else:
            results["errors"].append(f"Server import failed: {proc.stderr}")
    except subprocess.TimeoutExpired:
        results["errors"].append("Server startup timed out")
    except Exception as e:
        results["errors"].append(f"Server check failed: {e}")

    # Check which clients are configured
    for name, client in LLM_CLIENTS.items():
        config_path = client.get_config_path()
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
                # Navigate to MCP servers key
                current = config
                for key in client.mcp_key.split("."):
                    current = current.get(key, {})
                if "mgcp" in current:
                    results["clients_configured"].append(name)
            except (json.JSONDecodeError, AttributeError):
                pass

    return results


# ============================================================================
# Main CLI
# ============================================================================

def main():
    """CLI entry point for mgcp-init."""
    import argparse

    client_names = list(LLM_CLIENTS.keys())

    parser = argparse.ArgumentParser(
        description="Initialize MGCP for MCP-compatible LLM clients",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Supported clients: {', '.join(client_names)}

Examples:
  mgcp-init                          # Auto-detect and configure installed clients
  mgcp-init --client cursor          # Configure only Cursor
  mgcp-init --client claude-code --client cursor  # Configure multiple clients
  mgcp-init --list                   # List supported clients
  mgcp-init --detect                 # Show which clients are installed

For Claude Code users, this also creates project hooks:
  .claude/hooks/session-init.py       # Loads MGCP context on session start
  .claude/hooks/git-reminder.py       # Reminds to query lessons before git ops
  .claude/hooks/catalogue-reminder.py # Prompts to catalogue libraries/decisions
  .claude/hooks/mgcp-reminder.sh      # Reminds to save lessons after edits
  .claude/hooks/mgcp-precompact.sh    # Critical reminder before context compression
  .claude/settings.json               # Enables all hooks
        """,
    )

    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Project directory for Claude Code hooks (default: current directory)",
    )

    parser.add_argument(
        "--client", "-c",
        action="append",
        choices=client_names + ["all"],
        help="LLM client(s) to configure (can specify multiple). Default: auto-detect",
    )

    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all supported LLM clients",
    )

    parser.add_argument(
        "--detect", "-d",
        action="store_true",
        help="Detect which LLM clients are installed",
    )

    parser.add_argument(
        "--no-hooks",
        action="store_true",
        help="Skip creating Claude Code project hooks",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify MGCP setup is working correctly",
    )

    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Diagnose Claude Code configuration issues",
    )

    parser.add_argument(
        "--project-config",
        action="store_true",
        help="Also configure project-specific MCP server in ~/.claude.json (Claude Code only)",
    )

    args = parser.parse_args()

    # Handle --doctor
    if args.doctor:
        print("\n  Diagnosing Claude Code MGCP configuration...\n")
        results = diagnose_claude_code()

        # Global config
        print("  Global config:")
        g = results["global_config"]
        if g["status"] == "ok":
            status = "✓ configured" if g["mgcp_configured"] else "✗ MGCP not found"
            print(f"    {status}")
        elif g["status"] == "missing":
            print("    ✗ ~/.claude/settings.json not found")
        else:
            print(f"    ! {g['status']}")
        print(f"      {g['path']}")

        # Project configs with MGCP
        projects_with_mgcp = [p for p in results["project_configs"] if p["has_mgcp"]]
        if projects_with_mgcp:
            print(f"\n  Projects with MGCP configured ({len(projects_with_mgcp)}):")
            for proj in projects_with_mgcp:
                issues = " [ISSUES]" if proj["issues"] else ""
                print(f"    • {proj['path']}{issues}")
                for issue in proj["issues"]:
                    print(f"      ! {issue}")

        # Issues
        if results["issues"]:
            print("\n  Issues found:")
            for issue in results["issues"]:
                print(f"    ! {issue}")

        # Suggestions
        if results["suggestions"]:
            print("\n  Suggestions:")
            for suggestion in results["suggestions"]:
                print(f"    → {suggestion}")

        if not results["issues"] and results["global_config"]["mgcp_configured"]:
            print("\n  Status: Configuration looks good!\n")
        else:
            print("\n  Status: Issues found - see above\n")
        return

    # Handle --verify
    if args.verify:
        print("\n  Verifying MGCP setup...\n")
        results = verify_setup()

        print("  Checks:")
        print(f"    {'✓' if results['python_valid'] else '✗'} Python executable valid")
        print(f"    {'✓' if results['mgcp_importable'] else '✗'} MGCP module importable")
        print(f"    {'✓' if results['server_starts'] else '✗'} Server can start")

        if results['clients_configured']:
            print(f"\n  Configured clients: {', '.join(results['clients_configured'])}")
        else:
            print("\n  No clients configured yet. Run 'mgcp-init --client <name>' to configure.")

        if results['errors']:
            print("\n  Errors:")
            for e in results['errors']:
                print(f"    ! {e}")

        all_ok = results['python_valid'] and results['mgcp_importable'] and results['server_starts']
        print(f"\n  Status: {'Ready to use!' if all_ok else 'Issues found - see errors above'}\n")
        return

    # Handle --list
    if args.list:
        print("\n  Supported LLM Clients:\n")
        for name, client in LLM_CLIENTS.items():
            print(f"    {name:12} - {client.display_name} ({client.description})")
        print()
        return

    # Handle --detect
    if args.detect:
        installed = detect_installed_clients()
        print("\n  Detected LLM Clients:\n")
        for name, client in LLM_CLIENTS.items():
            status = "installed" if name in installed else "not found"
            marker = "+" if name in installed else "-"
            print(f"    {marker} {name:12} - {status}")
        print()
        return

    # Determine which clients to configure
    if args.client:
        if "all" in args.client:
            clients_to_configure = client_names
        else:
            clients_to_configure = args.client
    else:
        # Auto-detect
        clients_to_configure = detect_installed_clients()
        if not clients_to_configure:
            print("\n  No LLM clients detected. Use --client to specify manually.\n")
            print(f"  Supported: {', '.join(client_names)}\n")
            return

    project_dir = Path(args.directory).resolve()
    dry_run = args.dry_run

    if dry_run:
        print("\n  DRY RUN - no changes will be made\n")
    print("  Initializing MGCP")
    print(f"  Project: {project_dir}\n")

    # Configure each client
    print("  Configuring LLM clients:\n")
    configured_claude_code = False

    for client_name in clients_to_configure:
        client = LLM_CLIENTS[client_name]
        result = configure_client(client, dry_run=dry_run)

        status_icon = {
            "created": "+",
            "would_create": "?",
            "updated": "~",
            "would_update": "?",
            "unchanged": "=",
            "error": "!",
        }.get(result["status"], "?")

        print(f"    {status_icon} {client.display_name}: {result['message']}")
        print(f"      {result['path']}")

        if client_name == "claude-code":
            configured_claude_code = True

    # Create Claude Code hooks if applicable
    if configured_claude_code and not args.no_hooks:
        print("\n  Creating Claude Code project hooks:\n")
        hook_results = init_claude_hooks(project_dir, dry_run=dry_run)

        for f in hook_results["created"]:
            print(f"    + {f}")
        for f in hook_results["would_create"]:
            print(f"    ? {f} (would create)")
        for f in hook_results["updated"]:
            print(f"    ~ {f}")
        for f in hook_results["would_update"]:
            print(f"    ? {f} (would update)")
        for f in hook_results["skipped"]:
            print(f"    = {f} (already exists)")
        for e in hook_results["errors"]:
            print(f"    ! {e}")

    # Configure project-specific MCP server if requested
    if configured_claude_code and args.project_config:
        print("\n  Configuring project-specific MCP server:\n")
        proj_result = configure_claude_code_project(str(project_dir), dry_run=dry_run)

        status_icon = {
            "created": "+",
            "would_create": "?",
            "updated": "~",
            "would_update": "?",
            "unchanged": "=",
            "skipped": "-",
            "error": "!",
        }.get(proj_result["status"], "?")

        print(f"    {status_icon} {proj_result['message']}")
        print(f"      ~/.claude.json → projects.{project_dir}.mcpServers.mgcp")

    if dry_run:
        print("\n  Dry run complete. Run without --dry-run to apply changes.\n")
    else:
        print("\n  Done! Restart your LLM client for changes to take effect.\n")


if __name__ == "__main__":
    main()
