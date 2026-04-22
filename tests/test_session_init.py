"""Tests for session-init.py — the SessionStart bootstrap hook.

The hook is stdlib-only and emits JSON via stdout. We drive it as a
subprocess with a controlled HOME + CLAUDE_PROJECT_DIR so settings.json
under test can contain stale references for the detection path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = (
    Path(__file__).parent.parent
    / "src"
    / "mgcp"
    / "hook_templates"
    / "session-init.py"
)


def _run_hook(home: Path, project_dir: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        capture_output=True,
        text=True,
        env={
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": str(project_dir),
            "PATH": "/usr/bin:/bin",
        },
        check=True,
    )
    return json.loads(result.stdout)


def test_no_warning_when_settings_absent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Stale Hook References" not in ctx
    assert "Session Startup" in ctx


def test_no_warning_when_all_hook_scripts_exist(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".mgcp" / "hooks").mkdir(parents=True)
    # Create the file so the reference resolves
    script = home / ".mgcp" / "hooks" / "post-tool-dispatcher.py"
    script.write_text("#!/usr/bin/env python3\n")

    settings = {
        "hooks": {
            "PostToolUse": [{
                "hooks": [{"type": "command", "command": f"python3 {script}"}],
            }]
        }
    }
    (home / ".claude" / "settings.json").write_text(json.dumps(settings))

    project = tmp_path / "proj"
    project.mkdir()

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Stale Hook References" not in ctx


def test_warning_when_global_settings_references_missing_script(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".mgcp" / "hooks").mkdir(parents=True)
    missing = home / ".mgcp" / "hooks" / "mgcp-reminder.py"
    # Do NOT create it — this is the stale-reference case.

    settings = {
        "hooks": {
            "PostToolUse": [{
                "matcher": "Edit|Write",
                "hooks": [{"type": "command", "command": f"python3 {missing}"}],
            }]
        }
    }
    (home / ".claude" / "settings.json").write_text(json.dumps(settings))

    project = tmp_path / "proj"
    project.mkdir()

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Stale Hook References" in ctx
    assert "mgcp-reminder.py" in ctx
    assert "mgcp-init --force" in ctx


def test_warning_when_project_settings_references_missing_script(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)

    missing = project / ".claude" / "hooks" / "mgcp-reminder.py"
    # Intentionally not created.

    settings = {
        "hooks": {
            "PostToolUse": [{
                "hooks": [{"type": "command", "command": f"python3 {missing}"}],
            }]
        }
    }
    (project / ".claude" / "settings.json").write_text(json.dumps(settings))

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Stale Hook References" in ctx
    assert "mgcp-reminder.py" in ctx


def test_malformed_settings_does_not_crash(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{not valid json")

    project = tmp_path / "proj"
    project.mkdir()

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    # Hook should fail open — no warning, no crash.
    assert "Stale Hook References" not in ctx
    assert "Session Startup" in ctx


def test_relative_path_references_ignored(tmp_path):
    """Only absolute .py paths are checked — relative paths are ambiguous
    across hook events and would produce false positives."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    settings = {
        "hooks": {
            "PostToolUse": [{
                "hooks": [{"type": "command", "command": "python3 ./some-script.py"}],
            }]
        }
    }
    (home / ".claude" / "settings.json").write_text(json.dumps(settings))

    project = tmp_path / "proj"
    project.mkdir()

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Stale Hook References" not in ctx
