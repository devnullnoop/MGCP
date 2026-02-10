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
    Claude Code stores MCP settings in ~/.claude.json (user scope).

    This is the same on all platforms:
    - macOS/Linux: ~/.claude.json
    - Windows: %USERPROFILE%\\.claude.json

    The mcpServers key is at the root level for user-scope (global) config.
    """
    if sys.platform == "win32":
        return Path(os.environ.get("USERPROFILE", Path.home())) / ".claude.json"
    return Path.home() / ".claude.json"


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
# Claude Code Hooks (v2 - intent-based LLM self-routing)
# ============================================================================

HOOK_TEMPLATES_DIR = Path(__file__).parent / "hook_templates"

# Legacy hook filenames (v1) - removed during --force upgrade
LEGACY_HOOK_FILES = [
    "git-reminder.py",
    "catalogue-reminder.py",
    "task-start-reminder.py",
]

# v2 hook files: filename -> (hook event type, optional matcher)
V2_HOOK_FILES = {
    "session-init.py": ("SessionStart", None),
    "user-prompt-dispatcher.py": ("UserPromptSubmit", None),
    "mgcp-reminder.py": ("PostToolUse", "Edit|Write"),
    "mgcp-precompact.py": ("PreCompact", None),
}

VERSION_MARKER = ".mgcp-hook-version"


def _load_hook_template(filename: str) -> str:
    """Read a hook template from the package templates directory."""
    template_path = HOOK_TEMPLATES_DIR / filename
    return template_path.read_text()


def _get_hook_version() -> str:
    """Read the hook template version."""
    version_path = HOOK_TEMPLATES_DIR / "VERSION"
    return version_path.read_text().strip()


# Backward compat: HOOK_SCRIPT is still importable for tests
HOOK_SCRIPT = _load_hook_template("session-init.py")


def _build_hook_settings() -> dict:
    """Build the v2 HOOK_SETTINGS dict from V2_HOOK_FILES."""
    hooks: dict[str, list] = {}
    for filename, (event_type, matcher) in V2_HOOK_FILES.items():
        entry_hook = {
            "type": "command",
            "command": f"python3 $CLAUDE_PROJECT_DIR/.claude/hooks/{filename}",
        }
        entry: dict = {"hooks": [entry_hook]}
        if matcher:
            entry["matcher"] = matcher
        if event_type not in hooks:
            hooks[event_type] = []
        hooks[event_type].append(entry)
    return {
        "permissions": {
            "allow": ["mcp__mgcp__*"],
        },
        "hooks": hooks,
    }


HOOK_SETTINGS = _build_hook_settings()


def _merge_settings(existing: dict, mgcp_settings: dict) -> bool:
    """Merge MGCP settings into existing settings.json.

    - Adds mcp__mgcp__* to permissions.allow without clobbering existing perms
    - Checks for MGCP hooks by command string, not just by hook type presence
    - Appends MGCP hooks when the hook type exists but MGCP command is missing

    Returns True if any changes were made.
    """
    changed = False

    # Merge permissions.allow
    mgcp_perm = "mcp__mgcp__*"
    if "permissions" not in existing:
        existing["permissions"] = {}
    if "allow" not in existing["permissions"]:
        existing["permissions"]["allow"] = []
    if mgcp_perm not in existing["permissions"]["allow"]:
        existing["permissions"]["allow"].append(mgcp_perm)
        changed = True

    # Merge hooks
    if "hooks" not in existing:
        existing["hooks"] = {}

    for hook_type, hook_entries in mgcp_settings["hooks"].items():
        if hook_type not in existing["hooks"]:
            existing["hooks"][hook_type] = hook_entries
            changed = True
        else:
            # Hook type exists - check if MGCP hooks are already present
            existing_commands = set()
            for group in existing["hooks"][hook_type]:
                for h in group.get("hooks", []):
                    existing_commands.add(h.get("command", ""))

            for entry in hook_entries:
                for h in entry.get("hooks", []):
                    if h.get("command", "") not in existing_commands:
                        existing["hooks"][hook_type].append(entry)
                        changed = True
                        break  # Only append the entry once

    return changed


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


def init_claude_hooks(project_dir: Path, dry_run: bool = False, force: bool = False) -> dict:
    """
    Initialize Claude Code v2 hooks in a project directory.

    Creates:
    - .claude/hooks/session-init.py           (SessionStart hook)
    - .claude/hooks/user-prompt-dispatcher.py  (UserPromptSubmit hook)
    - .claude/hooks/mgcp-reminder.py           (PostToolUse hook)
    - .claude/hooks/mgcp-precompact.py         (PreCompact hook)
    - .claude/settings.json                    (Hook + permission configuration)

    All hooks are Python for cross-platform Windows compatibility.

    Args:
        project_dir: The project directory to initialize
        dry_run: If True, don't write changes, just report what would happen
        force: If True, overwrite existing hooks and remove legacy files

    Returns dict with status info.
    """
    results = {
        "created": [],
        "would_create": [],
        "updated": [],
        "would_update": [],
        "skipped": [],
        "removed": [],
        "would_remove": [],
        "errors": [],
        "upgrade_available": False,
    }

    hooks_dir = project_dir / ".claude" / "hooks"
    settings_file = project_dir / ".claude" / "settings.json"
    version_file = hooks_dir / VERSION_MARKER
    current_version = _get_hook_version()

    # Check/create .claude/hooks directory
    if not dry_run:
        hooks_dir.mkdir(parents=True, exist_ok=True)

    # Check existing version marker
    if not force and version_file.exists():
        installed_version = version_file.read_text().strip()
        if installed_version == current_version:
            # All up to date - still need to check individual files
            pass
        else:
            results["upgrade_available"] = True

    # Build hook file list from templates
    hook_files = []
    for filename in V2_HOOK_FILES:
        content = _load_hook_template(filename)
        hook_files.append((hooks_dir / filename, content))

    # Write all hook files
    for hook_file, hook_content in hook_files:
        if hook_file.exists():
            if force:
                if dry_run:
                    results["would_update"].append(str(hook_file))
                else:
                    hook_file.write_text(hook_content)
                    hook_file.chmod(0o755)
                    results["updated"].append(str(hook_file))
            else:
                results["skipped"].append(str(hook_file))
        else:
            if dry_run:
                results["would_create"].append(str(hook_file))
            else:
                hook_file.write_text(hook_content)
                hook_file.chmod(0o755)
                results["created"].append(str(hook_file))

    # Remove legacy hook files when force is set
    if force:
        for legacy_name in LEGACY_HOOK_FILES:
            legacy_file = hooks_dir / legacy_name
            if legacy_file.exists():
                if dry_run:
                    results["would_remove"].append(str(legacy_file))
                else:
                    legacy_file.unlink()
                    results["removed"].append(str(legacy_file))

    # Write version marker
    if not dry_run:
        version_file.write_text(current_version + "\n")

    # Write project settings.json - MERGE hooks and permissions
    if settings_file.exists():
        try:
            existing = json.loads(settings_file.read_text())

            if force:
                # Remove legacy hook commands from settings
                if "hooks" in existing:
                    for hook_type in list(existing["hooks"].keys()):
                        groups = existing["hooks"][hook_type]
                        for group in groups:
                            hooks_list = group.get("hooks", [])
                            group["hooks"] = [
                                h for h in hooks_list
                                if not any(
                                    legacy in h.get("command", "")
                                    for legacy in LEGACY_HOOK_FILES
                                )
                            ]
                        # Remove empty groups
                        existing["hooks"][hook_type] = [
                            g for g in groups if g.get("hooks")
                        ]
                        # Remove empty hook types
                        if not existing["hooks"][hook_type]:
                            del existing["hooks"][hook_type]

            needs_update = _merge_settings(existing, HOOK_SETTINGS)

            if needs_update or force:
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

    Checks ~/.claude.json for user-scope (global) and project-specific configs.

    Returns dict with diagnostic results.
    """
    results = {
        "user_config": {"path": None, "status": None, "mgcp_configured": False},
        "project_configs": [],
        "issues": [],
        "suggestions": [],
    }

    # Check user-scope config in ~/.claude.json
    config_path = _claude_code_path()
    results["user_config"]["path"] = str(config_path)

    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            results["user_config"]["status"] = "ok"

            # Check root-level mcpServers (user scope)
            if "mcpServers" in config and "mgcp" in config["mcpServers"]:
                results["user_config"]["mgcp_configured"] = True
                # Validate the config
                mgcp_cfg = config["mcpServers"]["mgcp"]
                if "command" in mgcp_cfg:
                    cmd = mgcp_cfg["command"]
                    if not Path(cmd).exists():
                        results["issues"].append(f"User config: Python path does not exist: {cmd}")
                        results["suggestions"].append("Run 'mgcp-init --client claude-code' to fix")

            # Also check project-specific configs
            if "projects" in config:
                for proj_path, proj_data in config["projects"].items():
                    proj_info = {
                        "path": proj_path,
                        "has_mgcp": False,
                        "config": None,
                        "issues": [],
                    }

                    if isinstance(proj_data, dict) and "mcpServers" in proj_data:
                        servers = proj_data["mcpServers"]
                        if isinstance(servers, dict) and "mgcp" in servers:
                            proj_info["has_mgcp"] = True
                            proj_info["config"] = servers["mgcp"]

                            # Validate
                            if "command" in servers["mgcp"]:
                                cmd = servers["mgcp"]["command"]
                                if not Path(cmd).exists():
                                    proj_info["issues"].append(f"Python path does not exist: {cmd}")

                    results["project_configs"].append(proj_info)

        except json.JSONDecodeError:
            results["user_config"]["status"] = "parse_error"
            results["issues"].append("Could not parse ~/.claude.json")
    else:
        results["user_config"]["status"] = "missing"
        results["issues"].append("~/.claude.json not found - Claude Code may not have been used yet")

    # Generate suggestions
    if not results["user_config"]["mgcp_configured"]:
        results["suggestions"].append("Run 'mgcp-init --client claude-code' to configure")

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
  mgcp-init --force /path/to/project # Upgrade hooks to v2 (overwrites existing)

For Claude Code users, this also creates v2 project hooks:
  .claude/hooks/session-init.py           # Intent-routing + context loading
  .claude/hooks/user-prompt-dispatcher.py # Scheduled reminders + workflow state
  .claude/hooks/mgcp-reminder.py          # Proof-based checkpoint after edits
  .claude/hooks/mgcp-precompact.py        # Critical save before context compression
  .claude/settings.json                   # Hook config + MGCP permissions
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
        "--force", "-f",
        action="store_true",
        help="Force overwrite existing hooks and remove legacy v1 hooks",
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

        # User-scope config
        print("  User config (~/.claude.json):")
        g = results["user_config"]
        if g["status"] == "ok":
            status = "✓ configured" if g["mgcp_configured"] else "✗ MGCP not found"
            print(f"    {status}")
        elif g["status"] == "missing":
            print("    ✗ ~/.claude.json not found")
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

        if not results["issues"] and results["user_config"]["mgcp_configured"]:
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
        print("\n  Creating Claude Code v2 project hooks:\n")
        hook_results = init_claude_hooks(project_dir, dry_run=dry_run, force=args.force)

        for f in hook_results["created"]:
            print(f"    + {f}")
        for f in hook_results["would_create"]:
            print(f"    ? {f} (would create)")
        for f in hook_results["updated"]:
            print(f"    ~ {f} (overwritten)")
        for f in hook_results["would_update"]:
            print(f"    ? {f} (would overwrite)")
        for f in hook_results["removed"]:
            print(f"    - {f} (legacy removed)")
        for f in hook_results["would_remove"]:
            print(f"    ? {f} (would remove legacy)")
        for f in hook_results["skipped"]:
            print(f"    = {f} (already exists)")
        for e in hook_results["errors"]:
            print(f"    ! {e}")

        if hook_results["upgrade_available"]:
            print("\n    Note: Hook upgrade available. Run with --force to upgrade to latest v2 hooks.")

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
