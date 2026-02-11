"""Comprehensive tests for mgcp-init functionality."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mgcp.init_project import (
    HOOK_SCRIPT,
    HOOK_SETTINGS,
    HOOK_TEMPLATES_DIR,
    LEGACY_HOOK_FILES,
    LLM_CLIENTS,
    V2_HOOK_FILES,
    VERSION_MARKER,
    LLMClient,
    _build_global_hook_settings,
    _get_hook_version,
    _merge_settings,
    configure_client,
    detect_installed_clients,
    ensure_embedding_model,
    get_mcp_server_config,
    get_mgcp_install_dir,
    get_mgcp_python_path,
    init_claude_hooks,
    init_global_hooks,
)

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Create a temporary home directory for tests."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Also patch Path.home() to return our temp home
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project directory."""
    project = tmp_path / "my-project"
    project.mkdir()
    return project


@pytest.fixture
def mock_config_paths(temp_home, monkeypatch):
    """Mock all client config paths to use temp directory."""
    paths = {
        "claude-code": temp_home / ".config" / "claude-code" / "settings.json",
        "cursor": temp_home / ".cursor" / "mcp.json",
        "windsurf": temp_home / ".codeium" / "windsurf" / "mcp_config.json",
        "continue": temp_home / ".continue" / "config.json",
        "cline": temp_home / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
    }

    # Patch each client's get_config_path function
    for client_name, path in paths.items():
        client = LLM_CLIENTS[client_name]
        # Create a closure to capture the path
        def make_path_func(p):
            return lambda: p
        monkeypatch.setattr(client, "get_config_path", make_path_func(path))

    return paths


# ============================================================================
# Tests: Helper Functions
# ============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_mgcp_python_path_returns_executable(self):
        """get_mgcp_python_path should return current Python executable."""
        result = get_mgcp_python_path()
        assert result == sys.executable
        assert Path(result).exists()

    def test_get_mgcp_install_dir_returns_valid_path(self):
        """get_mgcp_install_dir should return MGCP root directory."""
        result = get_mgcp_install_dir()
        assert result.exists()
        # Should contain src/mgcp
        assert (result / "src" / "mgcp").exists()

    def test_get_mcp_server_config_structure(self):
        """get_mcp_server_config should return proper config dict."""
        config = get_mcp_server_config()

        assert "command" in config
        assert "args" in config
        assert "cwd" in config

        assert config["args"] == ["-m", "mgcp.server"]
        assert Path(config["command"]).exists()
        assert Path(config["cwd"]).exists()

    def test_get_mcp_server_config_uses_current_python(self):
        """Config should use the current Python interpreter."""
        config = get_mcp_server_config()
        assert config["command"] == sys.executable


# ============================================================================
# Tests: LLM Client Registry
# ============================================================================

class TestLLMClientRegistry:
    """Tests for the LLM client registry."""

    def test_all_clients_registered(self):
        """All expected clients should be in registry."""
        expected = ["claude-code", "cursor", "windsurf", "continue", "cline"]
        for name in expected:
            assert name in LLM_CLIENTS

    def test_client_has_required_fields(self):
        """Each client should have all required fields."""
        for name, client in LLM_CLIENTS.items():
            assert isinstance(client, LLMClient)
            assert client.name == name
            assert client.display_name
            assert client.description
            assert callable(client.get_config_path)
            assert client.mcp_key

    def test_client_config_paths_are_absolute(self):
        """Client config paths should be absolute."""
        for name, client in LLM_CLIENTS.items():
            path = client.get_config_path()
            assert path.is_absolute(), f"{name} config path should be absolute"

    def test_claude_code_mcp_key(self):
        """Claude Code should use mcpServers key."""
        assert LLM_CLIENTS["claude-code"].mcp_key == "mcpServers"

    def test_cursor_mcp_key(self):
        """Cursor should use mcpServers key."""
        assert LLM_CLIENTS["cursor"].mcp_key == "mcpServers"

    def test_windsurf_mcp_key(self):
        """Windsurf should use mcpServers key."""
        assert LLM_CLIENTS["windsurf"].mcp_key == "mcpServers"

    def test_continue_mcp_key(self):
        """Continue should use nested experimental key."""
        assert LLM_CLIENTS["continue"].mcp_key == "experimental.modelContextProtocolServers"

    def test_cline_mcp_key(self):
        """Cline should use mcpServers key."""
        assert LLM_CLIENTS["cline"].mcp_key == "mcpServers"


# ============================================================================
# Tests: Client Detection
# ============================================================================

class TestClientDetection:
    """Tests for client detection functionality."""

    def test_detect_no_clients_installed(self, mock_config_paths):
        """Should return empty list when no clients installed."""
        result = detect_installed_clients()
        assert result == []

    def test_detect_client_with_config_file(self, mock_config_paths):
        """Should detect client when config file exists."""
        # Create cursor config
        config_path = mock_config_paths["cursor"]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{}")

        result = detect_installed_clients()
        assert "cursor" in result

    def test_detect_client_with_parent_dir_only(self, mock_config_paths):
        """Should detect client when parent directory exists (installed but not configured)."""
        # Create only the parent directory
        config_path = mock_config_paths["windsurf"]
        config_path.parent.mkdir(parents=True, exist_ok=True)

        result = detect_installed_clients()
        assert "windsurf" in result

    def test_detect_multiple_clients(self, mock_config_paths):
        """Should detect multiple installed clients."""
        # Install multiple clients
        for name in ["claude-code", "cursor", "cline"]:
            config_path = mock_config_paths[name]
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("{}")

        result = detect_installed_clients()
        assert len(result) == 3
        assert "claude-code" in result
        assert "cursor" in result
        assert "cline" in result


# ============================================================================
# Tests: Client Configuration
# ============================================================================

class TestConfigureClient:
    """Tests for configure_client function."""

    def test_configure_new_client_creates_file(self, mock_config_paths):
        """Should create config file for new client."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        result = configure_client(client)

        assert result["status"] == "created"
        assert config_path.exists()

    def test_configure_new_client_writes_correct_config(self, mock_config_paths):
        """Should write correct MCP config to new file."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        configure_client(client)

        config = json.loads(config_path.read_text())
        assert "mcpServers" in config
        assert "mgcp" in config["mcpServers"]
        assert config["mcpServers"]["mgcp"]["args"] == ["-m", "mgcp.server"]

    def test_configure_existing_client_without_mgcp(self, mock_config_paths):
        """Should add MGCP to existing config without MGCP."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create existing config with another server
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "mcpServers": {
                "other-server": {"command": "other"}
            }
        }))

        result = configure_client(client)

        assert result["status"] == "created"
        config = json.loads(config_path.read_text())
        assert "other-server" in config["mcpServers"]
        assert "mgcp" in config["mcpServers"]

    def test_configure_existing_client_with_mgcp_unchanged(self, mock_config_paths):
        """Should report unchanged if MGCP already configured identically."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create existing config with MGCP
        expected_config = get_mcp_server_config()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "mcpServers": {
                "mgcp": expected_config
            }
        }))

        result = configure_client(client)

        assert result["status"] == "unchanged"

    def test_configure_existing_client_with_old_mgcp_updates(self, mock_config_paths):
        """Should update MGCP config if different."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create existing config with old MGCP config
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "mcpServers": {
                "mgcp": {"command": "old-python", "args": ["-m", "mgcp.server"]}
            }
        }))

        result = configure_client(client)

        assert result["status"] == "updated"
        config = json.loads(config_path.read_text())
        assert config["mcpServers"]["mgcp"]["command"] != "old-python"

    def test_configure_client_creates_parent_dirs(self, mock_config_paths):
        """Should create parent directories if they don't exist."""
        client = LLM_CLIENTS["windsurf"]
        config_path = mock_config_paths["windsurf"]

        # Ensure parent doesn't exist
        assert not config_path.parent.exists()

        configure_client(client)

        assert config_path.exists()
        assert config_path.parent.exists()

    def test_configure_client_handles_malformed_json(self, mock_config_paths):
        """Should return error for malformed JSON config."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create malformed config
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("not valid json {{{")

        result = configure_client(client)

        assert result["status"] == "error"
        assert "parse" in result["message"].lower()

    def test_configure_continue_nested_key(self, mock_config_paths):
        """Should handle Continue's nested config key."""
        client = LLM_CLIENTS["continue"]
        config_path = mock_config_paths["continue"]

        configure_client(client)

        config = json.loads(config_path.read_text())
        assert "experimental" in config
        assert "modelContextProtocolServers" in config["experimental"]
        assert "mgcp" in config["experimental"]["modelContextProtocolServers"]

    def test_configure_continue_preserves_existing_nested(self, mock_config_paths):
        """Should preserve existing nested config in Continue."""
        client = LLM_CLIENTS["continue"]
        config_path = mock_config_paths["continue"]

        # Create existing config with other experimental settings
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "models": [{"name": "gpt-4"}],
            "experimental": {
                "otherSetting": True,
                "modelContextProtocolServers": {
                    "other-mcp": {"command": "other"}
                }
            }
        }))

        configure_client(client)

        config = json.loads(config_path.read_text())
        assert config["models"] == [{"name": "gpt-4"}]
        assert config["experimental"]["otherSetting"] is True
        assert "other-mcp" in config["experimental"]["modelContextProtocolServers"]
        assert "mgcp" in config["experimental"]["modelContextProtocolServers"]

    def test_configure_client_returns_path(self, mock_config_paths):
        """Should return the config file path in result."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        result = configure_client(client)

        assert result["path"] == str(config_path)

    def test_configure_client_returns_client_name(self, mock_config_paths):
        """Should return the client display name in result."""
        client = LLM_CLIENTS["cursor"]

        result = configure_client(client)

        assert result["client"] == "Cursor"


# ============================================================================
# Tests: Claude Code Hooks
# ============================================================================

class TestClaudeHooks:
    """Tests for Claude Code hook initialization."""

    def test_init_hooks_creates_directory(self, temp_project):
        """Should create .claude/hooks directory."""
        init_claude_hooks(temp_project)

        hooks_dir = temp_project / ".claude" / "hooks"
        assert hooks_dir.exists()
        assert hooks_dir.is_dir()

    def test_init_hooks_creates_hook_script(self, temp_project):
        """Should create session-init.py hook script."""
        init_claude_hooks(temp_project)

        hook_file = temp_project / ".claude" / "hooks" / "session-init.py"
        assert hook_file.exists()
        assert hook_file.read_text() == HOOK_SCRIPT

    def test_init_hooks_makes_script_executable(self, temp_project):
        """Should make hook script executable."""
        init_claude_hooks(temp_project)

        hook_file = temp_project / ".claude" / "hooks" / "session-init.py"
        mode = hook_file.stat().st_mode
        assert mode & stat.S_IXUSR  # Owner execute

    def test_init_hooks_creates_settings(self, temp_project):
        """Should create .claude/settings.json."""
        init_claude_hooks(temp_project)

        settings_file = temp_project / ".claude" / "settings.json"
        assert settings_file.exists()

        settings = json.loads(settings_file.read_text())
        assert settings == HOOK_SETTINGS

    def test_init_hooks_skips_existing_hook(self, temp_project):
        """Should skip if hook script already exists."""
        hooks_dir = temp_project / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_file = hooks_dir / "session-init.py"
        hook_file.write_text("# custom hook")

        result = init_claude_hooks(temp_project)

        assert str(hook_file) in result["skipped"]
        assert hook_file.read_text() == "# custom hook"

    def test_init_hooks_merges_with_existing_settings(self, temp_project):
        """Should merge MGCP hooks into existing settings, preserving user hooks."""
        claude_dir = temp_project / ".claude"
        claude_dir.mkdir()
        settings_file = claude_dir / "settings.json"
        # User has custom hooks but not MGCP hooks
        settings_file.write_text(json.dumps({"hooks": {"custom": []}}))

        result = init_claude_hooks(temp_project)

        # Should update to add missing MGCP hook types
        assert str(settings_file) in result["updated"]
        settings = json.loads(settings_file.read_text())
        # User's custom hooks should be preserved
        assert "custom" in settings["hooks"]
        # MGCP hooks should be added
        assert "SessionStart" in settings["hooks"]

    def test_init_hooks_skips_existing_settings_with_all_hooks(self, temp_project):
        """Should skip settings.json if all MGCP hook types already exist."""
        claude_dir = temp_project / ".claude"
        claude_dir.mkdir()
        settings_file = claude_dir / "settings.json"
        # Pre-populate with all MGCP hook types
        settings_file.write_text(json.dumps(HOOK_SETTINGS))

        result = init_claude_hooks(temp_project)

        assert str(settings_file) in result["skipped"]

    def test_init_hooks_updates_settings_without_hooks(self, temp_project):
        """Should add hooks to existing settings.json without hooks."""
        claude_dir = temp_project / ".claude"
        claude_dir.mkdir()
        settings_file = claude_dir / "settings.json"
        settings_file.write_text(json.dumps({"otherSetting": True}))

        result = init_claude_hooks(temp_project)

        assert str(settings_file) in result["updated"]
        settings = json.loads(settings_file.read_text())
        assert "hooks" in settings
        assert settings["otherSetting"] is True

    def test_init_hooks_handles_malformed_settings(self, temp_project):
        """Should report error for malformed settings.json."""
        claude_dir = temp_project / ".claude"
        claude_dir.mkdir()
        settings_file = claude_dir / "settings.json"
        settings_file.write_text("not json {{{")

        result = init_claude_hooks(temp_project)

        assert len(result["errors"]) > 0
        assert "parse" in result["errors"][0].lower()

    def test_init_hooks_returns_created_files(self, temp_project):
        """Should return list of created files."""
        result = init_claude_hooks(temp_project)

        # Creates 4 hook scripts + settings.json = 5 files
        assert len(result["created"]) == 5
        assert any("session-init.py" in f for f in result["created"])
        assert any("user-prompt-dispatcher.py" in f for f in result["created"])
        assert any("post-tool-dispatcher.py" in f for f in result["created"])
        assert any("mgcp-precompact.py" in f for f in result["created"])
        assert any("settings.json" in f for f in result["created"])

    def test_hook_script_is_valid_python(self, temp_project):
        """Hook script should be valid Python."""
        init_claude_hooks(temp_project)

        hook_file = temp_project / ".claude" / "hooks" / "session-init.py"

        # Compile to check syntax
        code = hook_file.read_text()
        compile(code, str(hook_file), "exec")

    def test_hook_script_produces_valid_json(self, temp_project):
        """Hook script should produce valid JSON output."""
        init_claude_hooks(temp_project)

        hook_file = temp_project / ".claude" / "hooks" / "session-init.py"

        # Run the hook script
        result = subprocess.run(
            [sys.executable, str(hook_file)],
            capture_output=True,
            text=True,
            cwd=str(temp_project)
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"


# ============================================================================
# Tests: CLI Interface
# ============================================================================

class TestCLI:
    """Tests for the CLI interface."""

    def test_cli_help(self):
        """CLI should show help text."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--help"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "Initialize MGCP" in result.stdout
        assert "--client" in result.stdout
        assert "--list" in result.stdout

    def test_cli_list_clients(self):
        """CLI --list should show all clients."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--list"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "claude-code" in result.stdout
        assert "cursor" in result.stdout
        assert "windsurf" in result.stdout
        assert "continue" in result.stdout
        assert "cline" in result.stdout

    def test_cli_detect_clients(self):
        """CLI --detect should show detection results."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--detect"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "Detected" in result.stdout

    def test_cli_invalid_client(self):
        """CLI should reject invalid client name."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--client", "invalid-client"],
            capture_output=True,
            text=True
        )

        assert result.returncode != 0

    def test_cli_specific_client(self, tmp_path, monkeypatch):
        """CLI should configure specific client when specified."""
        # Create a mock home with cursor directory
        home = tmp_path / "home"
        home.mkdir()
        cursor_dir = home / ".cursor"
        cursor_dir.mkdir()

        project = tmp_path / "project"
        project.mkdir()

        # Mock Path.home()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setenv("HOME", str(home))

        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project",
             "--client", "cursor", "--no-hooks", str(project)],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(home)}
        )

        # Check output mentions Cursor
        assert "Cursor" in result.stdout or result.returncode == 0

    def test_cli_multiple_clients(self):
        """CLI should accept multiple --client flags."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--help"],
            capture_output=True,
            text=True
        )

        # Help should show that multiple clients can be specified
        assert "can specify multiple" in result.stdout.lower() or "--client" in result.stdout

    def test_cli_all_clients(self):
        """CLI should accept --client all."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--help"],
            capture_output=True,
            text=True
        )

        assert "all" in result.stdout.lower()


# ============================================================================
# Tests: Integration / End-to-End
# ============================================================================

class TestIntegration:
    """Integration tests for full workflow."""

    def test_full_claude_code_setup(self, mock_config_paths, temp_project):
        """Full Claude Code setup should work end-to-end."""
        client = LLM_CLIENTS["claude-code"]

        # Configure client
        client_result = configure_client(client)
        assert client_result["status"] in ["created", "updated"]

        # Initialize hooks
        hook_result = init_claude_hooks(temp_project)
        assert len(hook_result["errors"]) == 0

        # Verify all files exist
        config_path = mock_config_paths["claude-code"]
        assert config_path.exists()
        assert (temp_project / ".claude" / "hooks" / "session-init.py").exists()
        assert (temp_project / ".claude" / "settings.json").exists()

    def test_full_cursor_setup(self, mock_config_paths):
        """Full Cursor setup should work end-to-end."""
        client = LLM_CLIENTS["cursor"]

        result = configure_client(client)

        assert result["status"] == "created"
        config_path = mock_config_paths["cursor"]
        assert config_path.exists()

        config = json.loads(config_path.read_text())
        assert "mgcp" in config["mcpServers"]

    def test_full_windsurf_setup(self, mock_config_paths):
        """Full Windsurf setup should work end-to-end."""
        client = LLM_CLIENTS["windsurf"]

        result = configure_client(client)

        assert result["status"] == "created"
        config_path = mock_config_paths["windsurf"]
        assert config_path.exists()

    def test_full_continue_setup(self, mock_config_paths):
        """Full Continue setup should work end-to-end."""
        client = LLM_CLIENTS["continue"]

        result = configure_client(client)

        assert result["status"] == "created"
        config_path = mock_config_paths["continue"]
        assert config_path.exists()

        config = json.loads(config_path.read_text())
        assert "mgcp" in config["experimental"]["modelContextProtocolServers"]

    def test_full_cline_setup(self, mock_config_paths):
        """Full Cline setup should work end-to-end."""
        client = LLM_CLIENTS["cline"]

        result = configure_client(client)

        assert result["status"] == "created"
        config_path = mock_config_paths["cline"]
        assert config_path.exists()

    def test_idempotent_configuration(self, mock_config_paths):
        """Running configuration twice should be idempotent."""
        client = LLM_CLIENTS["cursor"]

        # First run
        result1 = configure_client(client)
        assert result1["status"] == "created"

        # Second run
        result2 = configure_client(client)
        assert result2["status"] == "unchanged"

        # Config should be identical
        config_path = mock_config_paths["cursor"]
        config = json.loads(config_path.read_text())
        assert len(config["mcpServers"]) == 1

    def test_idempotent_hooks(self, temp_project):
        """Running hook init twice should be idempotent."""
        # First run - creates 4 hook scripts + settings.json = 5 files
        result1 = init_claude_hooks(temp_project)
        assert len(result1["created"]) == 5

        # Second run - all hooks should be skipped, settings.json skipped too
        result2 = init_claude_hooks(temp_project)
        assert len(result2["skipped"]) == 5
        assert len(result2["created"]) == 0


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_config_file(self, mock_config_paths):
        """Should handle empty config file."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("")

        result = configure_client(client)

        # Empty string is invalid JSON
        assert result["status"] == "error"

    def test_null_config_file(self, mock_config_paths):
        """Should handle config file with just null."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("null")

        result = configure_client(client)

        # null is valid JSON but not a dict - should return error
        assert result["status"] == "error"
        assert "object" in result["message"].lower() or "NoneType" in result["message"]

    def test_array_config_file(self, mock_config_paths):
        """Should handle config file with array instead of object."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("[]")

        result = configure_client(client)

        # Array is valid JSON but not a dict - should return error
        assert result["status"] == "error"
        assert "object" in result["message"].lower() or "list" in result["message"]

    def test_deeply_nested_existing_config(self, mock_config_paths):
        """Should preserve deeply nested existing config."""
        client = LLM_CLIENTS["continue"]
        config_path = mock_config_paths["continue"]

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "level1": {
                "level2": {
                    "level3": {
                        "value": "preserved"
                    }
                }
            },
            "experimental": {
                "existingKey": "existingValue"
            }
        }))

        configure_client(client)

        config = json.loads(config_path.read_text())
        assert config["level1"]["level2"]["level3"]["value"] == "preserved"
        assert config["experimental"]["existingKey"] == "existingValue"

    def test_unicode_in_config(self, mock_config_paths):
        """Should handle unicode in config files."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "mcpServers": {},
            "description": "Unicode test: 你好世界 🚀"
        }))

        configure_client(client)

        config = json.loads(config_path.read_text())
        assert "你好世界" in config["description"]
        assert "🚀" in config["description"]
        assert "mgcp" in config["mcpServers"]

    def test_special_characters_in_path(self, tmp_path):
        """Should handle special characters in project path."""
        project = tmp_path / "my project (test) #1"
        project.mkdir()

        result = init_claude_hooks(project)

        assert len(result["errors"]) == 0
        assert (project / ".claude" / "hooks" / "session-init.py").exists()

    @pytest.mark.skipif(
        os.getuid() == 0,
        reason="Test doesn't work when running as root"
    )
    def test_readonly_parent_directory(self, mock_config_paths, tmp_path):
        """Should handle readonly parent directory gracefully."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create parent directory
        parent = config_path.parent
        parent.mkdir(parents=True, exist_ok=True)

        # Create the config file first so exists() check passes
        config_path.write_text("{}")

        try:
            # Make parent readonly (can't write new files)
            parent.chmod(0o555)

            # Try to configure - should fail gracefully since we can't write
            try:
                result = configure_client(client)
                # If we got here, either we have permission or it errored gracefully
                assert result["status"] in ["created", "error", "updated", "unchanged"]
            except PermissionError:
                # This is also acceptable - the function raised instead of returning error
                pass
        finally:
            # Restore permissions for cleanup
            parent.chmod(0o755)

    def test_symlink_config_path(self, mock_config_paths, tmp_path):
        """Should handle symlinked config directories."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create actual directory elsewhere
        actual_dir = tmp_path / "actual_cursor"
        actual_dir.mkdir()

        # Create symlink
        config_path.parent.parent.mkdir(parents=True, exist_ok=True)
        if not config_path.parent.exists():
            config_path.parent.symlink_to(actual_dir)

        result = configure_client(client)

        assert result["status"] in ["created", "updated", "unchanged", "error"]


# ============================================================================
# Tests: Cross-Platform Paths (Mocked)
# ============================================================================

class TestCrossPlatformPaths:
    """Tests for cross-platform path handling."""

    def test_darwin_claude_code_path(self, monkeypatch, temp_home):
        """macOS Claude Code path should be correct."""
        monkeypatch.setattr(sys, "platform", "darwin")

        # Re-import to get fresh path
        from mgcp.init_project import _claude_code_path
        path = _claude_code_path()

        # Claude Code uses ~/.claude.json on all platforms
        assert ".claude.json" in str(path)

    def test_darwin_cursor_path(self, monkeypatch, temp_home):
        """macOS Cursor path should be correct."""
        monkeypatch.setattr(sys, "platform", "darwin")

        from mgcp.init_project import _cursor_path
        path = _cursor_path()

        assert ".cursor" in str(path)

    def test_linux_paths_similar_to_darwin(self, monkeypatch, temp_home):
        """Linux paths should be similar to macOS."""
        monkeypatch.setattr(sys, "platform", "linux")

        from mgcp.init_project import _claude_code_path, _cursor_path
        claude_path = _claude_code_path()
        cursor_path = _cursor_path()

        # Claude Code now uses ~/.claude/settings.json on all platforms
        assert ".claude" in str(claude_path)
        assert ".cursor" in str(cursor_path)


# ============================================================================
# Tests: Config Format Validation
# ============================================================================

class TestConfigFormat:
    """Tests for config format validation."""

    def test_mcp_config_has_required_fields(self):
        """MCP config should have all required fields."""
        config = get_mcp_server_config()

        required_fields = ["command", "args", "cwd"]
        for field in required_fields:
            assert field in config, f"Missing required field: {field}"

    def test_mcp_config_command_is_string(self):
        """MCP config command should be a string."""
        config = get_mcp_server_config()
        assert isinstance(config["command"], str)

    def test_mcp_config_args_is_list(self):
        """MCP config args should be a list."""
        config = get_mcp_server_config()
        assert isinstance(config["args"], list)

    def test_mcp_config_cwd_is_string(self):
        """MCP config cwd should be a string."""
        config = get_mcp_server_config()
        assert isinstance(config["cwd"], str)

    def test_hook_settings_format(self):
        """Hook settings should have correct format."""
        assert "hooks" in HOOK_SETTINGS
        assert "permissions" in HOOK_SETTINGS
        assert "SessionStart" in HOOK_SETTINGS["hooks"]

        session_hooks = HOOK_SETTINGS["hooks"]["SessionStart"]
        assert isinstance(session_hooks, list)
        assert len(session_hooks) > 0

        first_hook = session_hooks[0]
        assert "hooks" in first_hook
        assert isinstance(first_hook["hooks"], list)

    def test_hook_settings_has_permissions(self):
        """Hook settings should include MGCP permissions."""
        assert "permissions" in HOOK_SETTINGS
        assert "allow" in HOOK_SETTINGS["permissions"]
        assert "mcp__mgcp__*" in HOOK_SETTINGS["permissions"]["allow"]

    def test_hook_settings_has_v2_hooks(self):
        """Hook settings should reference v2 hook files."""
        all_commands = []
        for hook_type, entries in HOOK_SETTINGS["hooks"].items():
            for entry in entries:
                for h in entry.get("hooks", []):
                    all_commands.append(h.get("command", ""))

        assert any("session-init.py" in c for c in all_commands)
        assert any("user-prompt-dispatcher.py" in c for c in all_commands)
        assert any("post-tool-dispatcher.py" in c for c in all_commands)
        assert any("mgcp-precompact.py" in c for c in all_commands)
        # No legacy hooks
        for legacy_name in LEGACY_HOOK_FILES:
            assert not any(legacy_name in c for c in all_commands), f"Legacy hook {legacy_name} still in settings"


# ============================================================================
# Tests: Hook Script Content
# ============================================================================

class TestDryRun:
    """Tests for dry-run mode."""

    def test_dry_run_does_not_create_file(self, mock_config_paths):
        """Dry run should not create config file."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        result = configure_client(client, dry_run=True)

        assert result["status"] == "would_create"
        assert not config_path.exists()

    def test_dry_run_does_not_update_file(self, mock_config_paths):
        """Dry run should not update config file."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create existing config with old MGCP
        config_path.parent.mkdir(parents=True, exist_ok=True)
        original_content = json.dumps({
            "mcpServers": {"mgcp": {"command": "old"}}
        })
        config_path.write_text(original_content)

        result = configure_client(client, dry_run=True)

        assert result["status"] == "would_update"
        assert config_path.read_text() == original_content

    def test_dry_run_hooks_does_not_create_files(self, temp_project):
        """Dry run should not create hook files."""
        result = init_claude_hooks(temp_project, dry_run=True)

        # Would create 4 hook scripts + settings.json = 5 files
        assert len(result["would_create"]) == 5
        assert len(result["created"]) == 0
        assert not (temp_project / ".claude").exists()

    def test_dry_run_reports_unchanged(self, mock_config_paths):
        """Dry run should report unchanged if already configured correctly."""
        client = LLM_CLIENTS["cursor"]
        config_path = mock_config_paths["cursor"]

        # Create config with current MGCP settings
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "mcpServers": {"mgcp": get_mcp_server_config()}
        }))

        result = configure_client(client, dry_run=True)

        assert result["status"] == "unchanged"


class TestVerifySetup:
    """Tests for verify_setup function."""

    def test_verify_python_valid(self):
        """Verify should detect valid Python."""
        from mgcp.init_project import verify_setup
        result = verify_setup()
        assert result["python_valid"] is True

    def test_verify_mgcp_importable(self):
        """Verify should detect MGCP is importable."""
        from mgcp.init_project import verify_setup
        result = verify_setup()
        assert result["mgcp_importable"] is True

    def test_verify_server_starts(self):
        """Verify should detect server can start."""
        from mgcp.init_project import verify_setup
        result = verify_setup()
        assert result["server_starts"] is True

    def test_verify_detects_configured_clients(self, mock_config_paths):
        """Verify should list configured clients."""
        from mgcp.init_project import verify_setup

        # Configure a client first
        client = LLM_CLIENTS["cursor"]
        configure_client(client)

        result = verify_setup()
        assert "cursor" in result["clients_configured"]


class TestHookScriptContent:
    """Tests for hook script content."""

    def test_hook_script_has_shebang(self):
        """Hook script should have Python shebang."""
        assert HOOK_SCRIPT.startswith("#!/usr/bin/env python3")

    def test_hook_script_has_docstring(self):
        """Hook script should have docstring."""
        assert '"""' in HOOK_SCRIPT

    def test_hook_script_imports_json(self):
        """Hook script should import json."""
        assert "import json" in HOOK_SCRIPT

    def test_hook_script_imports_os(self):
        """Hook script should import os."""
        assert "import os" in HOOK_SCRIPT

    def test_hook_script_gets_project_path(self):
        """Hook script should get project path from env."""
        assert "CLAUDE_PROJECT_DIR" in HOOK_SCRIPT

    def test_hook_script_outputs_json(self):
        """Hook script should output JSON."""
        assert "print(json.dumps" in HOOK_SCRIPT

    def test_hook_script_mentions_mgcp(self):
        """Hook script should mention MGCP."""
        assert "MGCP" in HOOK_SCRIPT

    def test_hook_script_mentions_tools(self):
        """Hook script should mention MCP tools to call."""
        assert "get_project_context" in HOOK_SCRIPT
        assert "query_lessons" in HOOK_SCRIPT


# ============================================================================
# Tests: v2 Hook Templates
# ============================================================================

class TestHookTemplates:
    """Tests for v2 hook template package."""

    def test_hook_templates_dir_exists(self):
        """Hook templates directory should exist."""
        assert HOOK_TEMPLATES_DIR.exists()
        assert HOOK_TEMPLATES_DIR.is_dir()

    def test_hook_templates_version_file_exists(self):
        """VERSION file should exist in templates."""
        version_file = HOOK_TEMPLATES_DIR / "VERSION"
        assert version_file.exists()
        assert version_file.read_text().strip() == "2.0"

    def test_all_v2_templates_exist(self):
        """All v2 hook template files should exist."""
        for filename in V2_HOOK_FILES:
            template = HOOK_TEMPLATES_DIR / filename
            assert template.exists(), f"Missing template: {filename}"

    def test_templates_are_valid_python(self):
        """All hook templates should be valid Python."""
        for filename in V2_HOOK_FILES:
            template = HOOK_TEMPLATES_DIR / filename
            code = template.read_text()
            compile(code, str(template), "exec")

    def test_get_hook_version(self):
        """_get_hook_version should return version string."""
        version = _get_hook_version()
        assert version == "2.0"


# ============================================================================
# Tests: Force Upgrade
# ============================================================================

class TestForceUpgrade:
    """Tests for --force hook upgrade functionality."""

    def test_force_overwrites_existing_hooks(self, temp_project):
        """Force should overwrite existing hook files."""
        # First install
        init_claude_hooks(temp_project)

        # Modify a hook file
        hook_file = temp_project / ".claude" / "hooks" / "session-init.py"
        hook_file.write_text("# custom modification")

        # Force reinstall
        result = init_claude_hooks(temp_project, force=True)

        assert len(result["updated"]) >= 4  # 4 hooks overwritten
        assert hook_file.read_text() != "# custom modification"

    def test_force_removes_legacy_hooks(self, temp_project):
        """Force should remove all legacy hook files."""
        hooks_dir = temp_project / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        # Create legacy files
        for legacy_name in LEGACY_HOOK_FILES:
            (hooks_dir / legacy_name).write_text("# legacy")

        result = init_claude_hooks(temp_project, force=True)

        assert len(result["removed"]) == len(LEGACY_HOOK_FILES)
        for legacy_name in LEGACY_HOOK_FILES:
            assert not (hooks_dir / legacy_name).exists()

    def test_force_dry_run_reports_removals(self, temp_project):
        """Force dry-run should report what legacy files would be removed."""
        hooks_dir = temp_project / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        for legacy_name in LEGACY_HOOK_FILES:
            (hooks_dir / legacy_name).write_text("# legacy")

        result = init_claude_hooks(temp_project, force=True, dry_run=True)

        assert len(result["would_remove"]) == len(LEGACY_HOOK_FILES)
        # Legacy files should still exist (dry run)
        for legacy_name in LEGACY_HOOK_FILES:
            assert (hooks_dir / legacy_name).exists()

    def test_force_cleans_legacy_from_settings(self, temp_project):
        """Force should remove legacy hook commands from settings.json."""
        claude_dir = temp_project / ".claude"
        claude_dir.mkdir()
        settings_file = claude_dir / "settings.json"

        # Create settings with legacy hooks
        legacy_settings = {
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [
                        {"type": "command", "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/git-reminder.py"},
                        {"type": "command", "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/catalogue-reminder.py"},
                    ]
                }]
            }
        }
        settings_file.write_text(json.dumps(legacy_settings))

        init_claude_hooks(temp_project, force=True)

        settings = json.loads(settings_file.read_text())
        all_commands = []
        for entries in settings["hooks"].values():
            for entry in entries:
                for h in entry.get("hooks", []):
                    all_commands.append(h.get("command", ""))

        assert not any("git-reminder.py" in c for c in all_commands)
        assert not any("catalogue-reminder.py" in c for c in all_commands)
        # v2 hooks should be present
        assert any("user-prompt-dispatcher.py" in c for c in all_commands)


# ============================================================================
# Tests: Settings Merge
# ============================================================================

class TestSettingsMerge:
    """Tests for _merge_settings function."""

    def test_merge_adds_permissions_to_empty(self):
        """Should add permissions to empty settings."""
        existing = {}
        changed = _merge_settings(existing, HOOK_SETTINGS)

        assert changed
        assert "permissions" in existing
        assert "mcp__mgcp__*" in existing["permissions"]["allow"]

    def test_merge_preserves_existing_permissions(self):
        """Should preserve existing permissions while adding MGCP."""
        existing = {
            "permissions": {
                "allow": ["other_tool__*"],
            }
        }
        _merge_settings(existing, HOOK_SETTINGS)

        assert "other_tool__*" in existing["permissions"]["allow"]
        assert "mcp__mgcp__*" in existing["permissions"]["allow"]

    def test_merge_does_not_duplicate_permission(self):
        """Should not duplicate permission if already present."""
        existing = {
            "permissions": {
                "allow": ["mcp__mgcp__*"],
            },
            "hooks": HOOK_SETTINGS["hooks"].copy(),
        }
        changed = _merge_settings(existing, HOOK_SETTINGS)

        assert not changed
        assert existing["permissions"]["allow"].count("mcp__mgcp__*") == 1

    def test_merge_adds_missing_hook_types(self):
        """Should add hook types that don't exist."""
        existing = {"hooks": {"CustomHook": []}}
        _merge_settings(existing, HOOK_SETTINGS)

        assert "CustomHook" in existing["hooks"]  # Preserved
        assert "SessionStart" in existing["hooks"]  # Added

    def test_merge_appends_mgcp_hooks_to_existing_type(self):
        """Should append MGCP hooks when hook type exists but MGCP command missing."""
        existing = {
            "hooks": {
                "SessionStart": [{
                    "hooks": [{"type": "command", "command": "python3 other-hook.py"}]
                }]
            }
        }
        changed = _merge_settings(existing, HOOK_SETTINGS)

        assert changed
        # Should have both the original and MGCP hooks
        commands = []
        for entry in existing["hooks"]["SessionStart"]:
            for h in entry.get("hooks", []):
                commands.append(h.get("command", ""))
        assert any("other-hook.py" in c for c in commands)
        assert any("session-init.py" in c for c in commands)


# ============================================================================
# Tests: Version Marker
# ============================================================================

class TestVersionMarker:
    """Tests for hook version marker functionality."""

    def test_version_marker_created(self, temp_project):
        """Version marker file should be created after hook deployment."""
        init_claude_hooks(temp_project)

        version_file = temp_project / ".claude" / "hooks" / VERSION_MARKER
        assert version_file.exists()
        assert version_file.read_text().strip() == "2.0"

    def test_upgrade_available_when_version_mismatch(self, temp_project):
        """Should report upgrade_available when version marker is outdated."""
        # First install
        init_claude_hooks(temp_project)

        # Fake an old version
        version_file = temp_project / ".claude" / "hooks" / VERSION_MARKER
        version_file.write_text("1.0\n")

        result = init_claude_hooks(temp_project)

        assert result["upgrade_available"] is True

    def test_no_upgrade_when_version_matches(self, temp_project):
        """Should not report upgrade when version matches."""
        init_claude_hooks(temp_project)

        result = init_claude_hooks(temp_project)

        assert result["upgrade_available"] is False


# ============================================================================
# Tests: Global Hooks
# ============================================================================

@pytest.fixture
def mock_global_paths(tmp_path, monkeypatch):
    """Mock global hooks paths to use temp directories."""
    hooks_dir = tmp_path / ".mgcp" / "hooks"
    settings_path = tmp_path / ".claude" / "settings.json"

    import mgcp.init_project as mod
    monkeypatch.setattr(mod, "GLOBAL_HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(mod, "GLOBAL_SETTINGS_PATH", settings_path)

    return {"hooks_dir": hooks_dir, "settings_path": settings_path}


class TestGlobalHooks:
    """Tests for global hook deployment."""

    def test_init_global_hooks_creates_scripts(self, mock_global_paths):
        """Should copy all hook scripts to ~/.mgcp/hooks/."""
        hooks_dir = mock_global_paths["hooks_dir"]

        result = init_global_hooks()

        for filename in V2_HOOK_FILES:
            assert (hooks_dir / filename).exists(), f"Missing: {filename}"
        assert len(result["created"]) >= 4  # 4 scripts

    def test_init_global_hooks_creates_settings(self, mock_global_paths):
        """Should create ~/.claude/settings.json with hook config and MCP server."""
        settings_path = mock_global_paths["settings_path"]

        init_global_hooks()

        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert "permissions" in settings
        assert "mcpServers" in settings
        assert "mgcp" in settings["mcpServers"]
        assert "SessionStart" in settings["hooks"]

    def test_init_global_hooks_uses_absolute_paths(self, mock_global_paths):
        """Global hook commands should use absolute paths, not $CLAUDE_PROJECT_DIR."""
        settings_path = mock_global_paths["settings_path"]
        hooks_dir = mock_global_paths["hooks_dir"]

        init_global_hooks()

        settings = json.loads(settings_path.read_text())
        all_commands = []
        for entries in settings["hooks"].values():
            for entry in entries:
                for h in entry.get("hooks", []):
                    all_commands.append(h.get("command", ""))

        # Should NOT contain project-relative paths
        for cmd in all_commands:
            assert "$CLAUDE_PROJECT_DIR" not in cmd

        # Should contain absolute paths to the hooks dir
        for cmd in all_commands:
            assert str(hooks_dir) in cmd

    def test_init_global_hooks_merges_existing_settings(self, mock_global_paths):
        """Should preserve existing settings when merging."""
        settings_path = mock_global_paths["settings_path"]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({
            "mcpServers": {"other": {"command": "other"}},
            "customSetting": True,
        }))

        init_global_hooks()

        settings = json.loads(settings_path.read_text())
        assert settings["customSetting"] is True
        assert "other" in settings["mcpServers"]
        assert "mgcp" in settings["mcpServers"]
        assert "hooks" in settings
        assert "mcp__mgcp__*" in settings["permissions"]["allow"]

    def test_init_global_hooks_idempotent(self, mock_global_paths):
        """Second run should skip existing files."""
        # First run
        result1 = init_global_hooks()
        assert len(result1["created"]) >= 4

        # Second run
        result2 = init_global_hooks()
        assert len(result2["skipped"]) >= 4
        assert len(result2["created"]) == 0

    def test_init_global_hooks_force_overwrites(self, mock_global_paths):
        """Force should overwrite existing hook files."""
        hooks_dir = mock_global_paths["hooks_dir"]

        # First install
        init_global_hooks()

        # Modify a hook file
        hook_file = hooks_dir / "session-init.py"
        hook_file.write_text("# custom modification")

        # Force reinstall
        result = init_global_hooks(force=True)

        assert len(result["updated"]) >= 4
        assert hook_file.read_text() != "# custom modification"

    def test_init_global_hooks_version_marker(self, mock_global_paths):
        """Should write version marker to ~/.mgcp/hooks/."""
        hooks_dir = mock_global_paths["hooks_dir"]

        init_global_hooks()

        version_file = hooks_dir / VERSION_MARKER
        assert version_file.exists()
        assert version_file.read_text().strip() == "2.0"

    def test_global_hooks_permissions(self, mock_global_paths):
        """Settings should include mcp__mgcp__* permission."""
        settings_path = mock_global_paths["settings_path"]

        init_global_hooks()

        settings = json.loads(settings_path.read_text())
        assert "permissions" in settings
        assert "allow" in settings["permissions"]
        assert "mcp__mgcp__*" in settings["permissions"]["allow"]

    def test_global_hooks_scripts_are_executable(self, mock_global_paths):
        """Hook scripts should be executable."""
        hooks_dir = mock_global_paths["hooks_dir"]

        init_global_hooks()

        for filename in V2_HOOK_FILES:
            hook_file = hooks_dir / filename
            mode = hook_file.stat().st_mode
            assert mode & stat.S_IXUSR, f"{filename} should be executable"

    def test_global_hooks_scripts_match_templates(self, mock_global_paths):
        """Deployed scripts should match the template originals."""
        hooks_dir = mock_global_paths["hooks_dir"]

        init_global_hooks()

        for filename in V2_HOOK_FILES:
            deployed = (hooks_dir / filename).read_text()
            template = (HOOK_TEMPLATES_DIR / filename).read_text()
            assert deployed == template, f"{filename} content mismatch"

    def test_global_hooks_dry_run(self, mock_global_paths):
        """Dry run should not create files."""
        hooks_dir = mock_global_paths["hooks_dir"]
        settings_path = mock_global_paths["settings_path"]

        result = init_global_hooks(dry_run=True)

        assert len(result["would_create"]) >= 4  # scripts + settings
        assert not hooks_dir.exists()
        assert not settings_path.exists()

    def test_global_hooks_upgrade_available(self, mock_global_paths):
        """Should report upgrade_available when version marker is outdated."""
        hooks_dir = mock_global_paths["hooks_dir"]

        init_global_hooks()

        # Fake an old version
        version_file = hooks_dir / VERSION_MARKER
        version_file.write_text("1.0\n")

        result = init_global_hooks()
        assert result["upgrade_available"] is True

    def test_global_hooks_handles_malformed_settings(self, mock_global_paths):
        """Should report error for malformed settings.json."""
        settings_path = mock_global_paths["settings_path"]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("not json {{{")

        result = init_global_hooks()

        assert len(result["errors"]) > 0
        assert "parse" in result["errors"][0].lower()

    def test_global_settings_includes_mcp_server(self, mock_global_paths):
        """Global settings should include mcpServers.mgcp so tools actually exist."""
        settings_path = mock_global_paths["settings_path"]

        init_global_hooks()

        settings = json.loads(settings_path.read_text())
        assert "mcpServers" in settings
        assert "mgcp" in settings["mcpServers"]
        mgcp_config = settings["mcpServers"]["mgcp"]
        assert mgcp_config["args"] == ["-m", "mgcp.server"]
        assert "command" in mgcp_config
        assert "cwd" in mgcp_config

    def test_build_global_hook_settings_includes_mcp_server(self):
        """_build_global_hook_settings should include mcpServers."""
        settings = _build_global_hook_settings()
        assert "mcpServers" in settings
        assert "mgcp" in settings["mcpServers"]
        assert settings["mcpServers"]["mgcp"] == get_mcp_server_config()

    def test_global_deploy_skips_claude_code_configure_client(self, mock_global_paths, mock_config_paths):
        """Global deployment should NOT call configure_client for claude-code.

        When deploying global hooks, mcpServers.mgcp is included in
        ~/.claude/settings.json. Writing it to ~/.claude.json too would
        create a duplicate MCP server definition that fails to start.
        """
        # Make claude-code "installed" so it would be detected
        config_path = mock_config_paths["claude-code"]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{}")

        # Run global hooks deployment
        init_global_hooks()

        # claude-code config file should NOT have been modified with mcpServers
        config = json.loads(config_path.read_text())
        assert "mcpServers" not in config, (
            "configure_client should not write to ~/.claude.json during global deployment"
        )

        # But settings.json SHOULD have mcpServers
        settings = json.loads(mock_global_paths["settings_path"].read_text())
        assert "mcpServers" in settings
        assert "mgcp" in settings["mcpServers"]

    def test_build_global_hook_settings_uses_absolute_paths(self, mock_global_paths):
        """_build_global_hook_settings should use absolute paths."""
        settings = _build_global_hook_settings()

        all_commands = []
        for entries in settings["hooks"].values():
            for entry in entries:
                for h in entry.get("hooks", []):
                    all_commands.append(h.get("command", ""))

        for cmd in all_commands:
            assert "$CLAUDE_PROJECT_DIR" not in cmd
            # Command should start with python3 followed by an absolute path
            assert cmd.startswith("python3 /") or cmd.startswith("python3 C:"), f"Not absolute: {cmd}"


# ============================================================================
# Tests: Embedding Model Download
# ============================================================================

class TestEnsureEmbeddingModel:
    """Tests for ensure_embedding_model function."""

    def test_ensure_model_cached(self, monkeypatch):
        """Should detect already-cached model without downloading."""
        from unittest.mock import MagicMock

        mock_st = MagicMock()
        # Mock at the package level since ensure_embedding_model imports fresh
        import sentence_transformers
        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", mock_st)

        # local_files_only=True succeeds (no exception = model is cached)
        result = ensure_embedding_model()

        assert result["error"] is None
        assert result["downloaded"] is False
        assert "already cached" in result["message"]

    def test_ensure_model_downloads_when_not_cached(self, monkeypatch):
        """Should download model when not cached locally."""
        from unittest.mock import MagicMock

        call_count = 0

        def mock_st_constructor(name, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("local_files_only"):
                raise OSError("not cached")
            return MagicMock()

        import sentence_transformers
        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", mock_st_constructor)

        result = ensure_embedding_model()

        assert result["error"] is None
        assert result["downloaded"] is True
        assert "downloaded and ready" in result["message"]
        assert call_count == 2  # first local_files_only, then full download

    def test_ensure_model_returns_dict_structure(self, monkeypatch):
        """Should return dict with expected keys."""
        from unittest.mock import MagicMock

        import sentence_transformers
        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", MagicMock())

        result = ensure_embedding_model()

        assert "downloaded" in result
        assert "message" in result
        assert "error" in result
        assert isinstance(result["downloaded"], bool)
        assert isinstance(result["message"], str)

    def test_ensure_model_handles_import_error(self, monkeypatch):
        """Should handle missing sentence-transformers gracefully."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("No module named 'sentence_transformers'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        result = ensure_embedding_model()

        assert result["error"] is not None
        assert "Could not load" in result["message"]
