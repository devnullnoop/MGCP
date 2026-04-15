"""Tests for pre-tool-dispatcher.py — the PreToolUse enforcement hook.

Loads the hook as a module via importlib so we can unit-test the detector
without shelling out to python3 on every case.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = (
    Path(__file__).parent.parent
    / "src"
    / "mgcp"
    / "hook_templates"
    / "pre-tool-dispatcher.py"
)


@pytest.fixture(scope="module")
def hook_module():
    spec = importlib.util.spec_from_file_location("pre_tool_dispatcher", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCommandDetector:
    """The token walker must distinguish `git commit` the command from
    `git commit` as a string inside an argument."""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m foo",
            "git push",
            "git push origin main",
            "make build && git push origin main",
            "run_tests.sh; git commit -am 'ok'",
            "(cd subdir && git commit -m msg)",
        ],
    )
    def test_matches_real_git_invocations(self, hook_module, command):
        assert hook_module._command_invokes_enforced_git(command) is True

    @pytest.mark.parametrize(
        "command",
        [
            # git commit appears inside a quoted argument — not a command
            "grep -r 'git commit' docs/",
            'echo "how to git commit properly" > guide.txt',
            "cat README.md | grep 'git push'",
            # git subcommand we don't enforce
            "git status",
            "git log --oneline",
            "git diff HEAD~1",
            # command doesn't start with git at all
            "python3 train.py",
            "pytest tests/",
            # 'git' appears but not as a shell command
            "echo git commit",  # echo is the command, rest are args
        ],
    )
    def test_does_not_match_non_invocations(self, hook_module, command):
        assert hook_module._command_invokes_enforced_git(command) is False

    def test_unparseable_command_fails_open(self, hook_module):
        # Unterminated quote — shlex raises ValueError. Hook falls open.
        assert hook_module._command_invokes_enforced_git("git commit -m 'oops") is False


class TestEnforcement:
    """End-to-end: run the hook as a subprocess and check permission decisions."""

    def _run(self, hook_input: dict, state: dict, tmp_path: Path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            env={"MGCP_STATE_FILE": str(state_file), "PATH": "/usr/bin:/bin"},
        )
        return result

    def test_git_commit_without_query_lessons_is_denied(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
            {"turn_query_lessons_called": False, "turn_bypass": False},
            tmp_path,
        )
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_git_commit_after_query_lessons_is_allowed(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
            {"turn_query_lessons_called": True, "turn_bypass": False},
            tmp_path,
        )
        assert r.stdout.strip() == ""  # no output = allow

    def test_bypass_token_allows_through(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git push"}},
            {"turn_query_lessons_called": False, "turn_bypass": True},
            tmp_path,
        )
        assert r.stdout.strip() == ""

    def test_non_bash_tool_is_allowed(self, tmp_path):
        r = self._run(
            {"tool_name": "Edit", "tool_input": {"file_path": "x", "old_string": "git commit", "new_string": "y"}},
            {"turn_query_lessons_called": False, "turn_bypass": False},
            tmp_path,
        )
        assert r.stdout.strip() == ""

    def test_missing_state_file_fails_open(self, tmp_path):
        # State file doesn't exist — hook should NOT crash, and should deny
        # (default flags treat missing state as "query_lessons not called").
        state_file = tmp_path / "does-not-exist.json"
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}),
            capture_output=True,
            text=True,
            env={"MGCP_STATE_FILE": str(state_file), "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_malformed_json_input_fails_open(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="not valid json{{{",
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""