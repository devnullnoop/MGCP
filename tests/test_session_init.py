"""Tests for session-init.py — the SessionStart bootstrap hook.

The hook is stdlib-only and emits JSON via stdout. We drive it as a
subprocess with a controlled HOME + CLAUDE_PROJECT_DIR so settings.json
under test can contain stale references for the detection path.
"""
from __future__ import annotations

import json
import sqlite3
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


# ---------------------------------------------------------------------------
# REM-overdue detector (v2.7)
# ---------------------------------------------------------------------------


def _seed_rem_db(home: Path, project_path: str, session_count: int, rows: list[dict]):
    """Create ~/.mgcp/lessons.db with project_contexts + rem_state rows.

    rows: list of dicts with keys: operation, last_run_session,
    next_due_session.
    """
    db_dir = home / ".mgcp"
    db_dir.mkdir(parents=True, exist_ok=True)
    db = db_dir / "lessons.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE project_contexts (
                project_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL,
                project_path TEXT NOT NULL UNIQUE,
                catalogue JSON NOT NULL DEFAULT '{}',
                todos JSON NOT NULL DEFAULT '[]',
                active_files JSON NOT NULL DEFAULT '[]',
                recent_decisions JSON NOT NULL DEFAULT '[]',
                last_session_id TEXT,
                last_accessed TEXT NOT NULL,
                session_count INTEGER DEFAULT 0,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE rem_state (
                project_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                last_run_session INTEGER NOT NULL DEFAULT 0,
                last_run_timestamp TEXT NOT NULL,
                last_run_result JSON,
                next_due_session INTEGER,
                PRIMARY KEY (project_id, operation)
            )
            """
        )
        conn.execute(
            "INSERT INTO project_contexts "
            "(project_id, project_name, project_path, last_accessed, session_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test-id", "test", project_path, "2026-01-01T00:00:00Z", session_count),
        )
        for r in rows:
            conn.execute(
                "INSERT INTO rem_state "
                "(project_id, operation, last_run_session, last_run_timestamp, "
                " next_due_session) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    r.get("project_id", "test-id"),
                    r["operation"],
                    r.get("last_run_session", 0),
                    "2026-01-01T00:00:00Z",
                    r.get("next_due_session"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_no_rem_warning_when_db_absent(tmp_path):
    """No DB at all — fail open, no warning."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "REM Operations Overdue" not in ctx
    assert "Session Startup" in ctx


def test_no_rem_warning_when_no_overdue(tmp_path):
    """All next_due_session > session_count — nothing overdue."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _seed_rem_db(
        home,
        str(project),
        session_count=5,
        rows=[
            {"operation": "staleness_scan", "last_run_session": 5, "next_due_session": 10},
            {"operation": "duplicate_detection", "last_run_session": 5, "next_due_session": 15},
        ],
    )

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "REM Operations Overdue" not in ctx


def test_rem_warning_when_operation_overdue(tmp_path):
    """session_count >= next_due_session → warning fires with operation name."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _seed_rem_db(
        home,
        str(project),
        session_count=20,
        rows=[
            {"operation": "intent_calibration", "last_run_session": 10, "next_due_session": 16},
        ],
    )

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "REM Operations Overdue" in ctx
    assert "intent_calibration" in ctx
    assert "rem_run" in ctx


def test_rem_warning_lists_all_overdue(tmp_path):
    """Multiple overdue operations all appear, non-overdue ones do not."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _seed_rem_db(
        home,
        str(project),
        session_count=20,
        rows=[
            {"operation": "staleness_scan", "last_run_session": 10, "next_due_session": 15},
            {"operation": "duplicate_detection", "last_run_session": 10, "next_due_session": 18},
            {"operation": "context_summary", "last_run_session": 10, "next_due_session": 25},
        ],
    )

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "REM Operations Overdue" in ctx
    assert "staleness_scan" in ctx
    assert "duplicate_detection" in ctx
    assert "context_summary" not in ctx


def test_no_rem_warning_from_another_projects_schedule(tmp_path):
    """This project IS in the DB, but the overdue row belongs to someone else.

    The rows are scoped by project_id, so a neighbouring project being nine
    sessions overdue must not raise a warning here.
    """
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _seed_rem_db(
        home,
        str(project),
        session_count=20,
        rows=[
            {
                "project_id": "some-other-project",
                "operation": "staleness_scan",
                "last_run_session": 5,
                "next_due_session": 11,
            },
        ],
    )

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "REM Operations Overdue" not in ctx
    assert "staleness_scan" not in ctx


def test_no_rem_warning_when_project_not_in_db(tmp_path):
    """DB has rem_state for a different project — fail open."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _seed_rem_db(
        home,
        "/some/other/project",
        session_count=20,
        rows=[
            {"operation": "staleness_scan", "last_run_session": 10, "next_due_session": 15},
        ],
    )

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "REM Operations Overdue" not in ctx


def test_malformed_db_fails_open(tmp_path):
    """Garbage in lessons.db — no crash, no warning."""
    home = tmp_path / "home"
    (home / ".mgcp").mkdir(parents=True)
    (home / ".mgcp" / "lessons.db").write_bytes(b"not a sqlite database")

    project = tmp_path / "proj"
    project.mkdir()

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "REM Operations Overdue" not in ctx
    assert "Session Startup" in ctx


def test_rem_warning_message_includes_toggle_hint(tmp_path):
    """The action footer should mention toggle_enforcement_rule for opt-in."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    _seed_rem_db(
        home,
        str(project),
        session_count=20,
        rows=[{"operation": "intent_calibration", "last_run_session": 10, "next_due_session": 16}],
    )

    output = _run_hook(home, project)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "toggle_enforcement_rule" in ctx
    assert "rem-required-before-commit" in ctx
