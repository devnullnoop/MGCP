#!/usr/bin/env python3
"""SessionStart hook for MGCP v2.7.

Injects only session-start bootstrap instructions:
- Read soliloquy / get project context / query lessons
- Workflow execution discipline
- Stale hook reference detection (tells user to run mgcp-init --force)
- REM operations overdue detection (tells me to call rem_run)

Intent classification + action mapping is deliberately NOT injected here.
The UserPromptSubmit dispatcher re-injects the (classifier + inline
actions) block on every message from ``rendered.dispatcher_routing`` in
``intent_config.json`` — that one copy survives context compaction and
makes a duplicate SessionStart copy pure token noise.
"""
import json
import os
import shlex
import sqlite3
from pathlib import Path

project_path = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def _find_stale_hook_refs():
    """Scan settings.json files for hook commands pointing at missing scripts.

    Returns a list of (settings_path, missing_script) tuples. Absolute-path
    .py references only — relative paths are ambiguous across hook events.
    """
    candidates = [
        Path.home() / ".claude" / "settings.json",
        Path(project_path) / ".claude" / "settings.json",
    ]
    stale = []
    seen = set()
    for settings_file in candidates:
        if not settings_file.exists():
            continue
        try:
            data = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        for entries in hooks.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for h in entry.get("hooks", []) or []:
                    cmd = h.get("command", "") if isinstance(h, dict) else ""
                    if not cmd:
                        continue
                    try:
                        tokens = shlex.split(cmd)
                    except ValueError:
                        tokens = cmd.split()
                    for token in tokens:
                        if not token.endswith(".py"):
                            continue
                        expanded = os.path.expanduser(os.path.expandvars(token))
                        if not os.path.isabs(expanded):
                            continue
                        if Path(expanded).exists():
                            continue
                        key = (str(settings_file), expanded)
                        if key in seen:
                            continue
                        seen.add(key)
                        stale.append(key)
    return stale


def _find_overdue_rem_operations():
    """Return overdue REM operations for the current project.

    Reads ``~/.mgcp/lessons.db`` (override with ``MGCP_DATA_DIR``). For each
    rem_state row, an operation is overdue when
    ``project_contexts.session_count >= rem_state.next_due_session``.

    Stdlib sqlite3 only, opens in read-only URI mode with a 2-second
    timeout to avoid blocking the live MCP server's writer. Fails open
    (returns ``[]``) on any error: missing DB, missing table, missing
    project row, malformed schema. Enforcement is a safety net, not a
    tripwire.

    Returns a list of dicts: ``{operation, last_run_session,
    next_due_session, session_count}``.
    """
    base = os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    db_path = Path(base) / "lessons.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        try:
            ctx_row = conn.execute(
                "SELECT session_count FROM project_contexts WHERE project_path = ?",
                (project_path,),
            ).fetchone()
        except sqlite3.Error:
            return []
        if ctx_row is None:
            return []
        session_count = ctx_row["session_count"] or 0
        try:
            rows = conn.execute(
                "SELECT operation, last_run_session, next_due_session "
                "FROM rem_state"
            ).fetchall()
        except sqlite3.Error:
            return []
    finally:
        conn.close()

    overdue = []
    for r in rows:
        nxt = r["next_due_session"]
        if nxt is None:
            continue
        if session_count >= nxt:
            overdue.append({
                "operation": r["operation"],
                "last_run_session": r["last_run_session"] or 0,
                "next_due_session": nxt,
                "session_count": session_count,
            })
    return overdue


warning_blocks = []

stale = _find_stale_hook_refs()
if stale:
    lines = ["## ⚠️ Stale Hook References Detected", ""]
    lines.append("The following hook scripts are configured but missing on disk:")
    lines.append("")
    for settings_file, missing in stale:
        lines.append(f"- `{missing}`")
        lines.append(f"  (referenced in `{settings_file}`)")
    lines.append("")
    lines.append(
        "These produce `hook returned blocking error` / Errno 2 noise on every "
        "matching tool call. The file write/edit itself still succeeded — "
        "PostToolUse hooks cannot actually block — but the error surfaces "
        "in the UI."
    )
    lines.append("")
    lines.append(
        "**Fix:** tell the user to run `mgcp-init --force` from the MGCP repo. "
        "That re-deploys current hooks and scrubs stale `settings.json` entries."
    )
    warning_blocks.append("\n".join(lines))

overdue = _find_overdue_rem_operations()
if overdue:
    lines = ["## ⚠️ REM Operations Overdue", ""]
    lines.append(
        "The following REM cycle operations are past their next_due_session "
        "for this project. REM has no auto-trigger; without manual invocation "
        "the schedule drifts unboundedly."
    )
    lines.append("")
    for op in overdue:
        gap = op["session_count"] - op["next_due_session"]
        lines.append(
            f"- `{op['operation']}` — last run session "
            f"{op['last_run_session']}, due at session "
            f"{op['next_due_session']} (current session "
            f"{op['session_count']}, {gap} session{'s' if gap != 1 else ''} overdue)"
        )
    lines.append("")
    lines.append(
        "**Action:** call `mcp__mgcp__rem_run` with no arguments to run every "
        "due operation now, READ the findings, and act on the high-signal "
        "ones (intent_calibration suggestions, duplicate merges, staleness "
        "warnings). If commit-time enforcement of REM execution would help, "
        "enable the seeded-but-default-off `rem-required-before-commit` rule "
        "via `mcp__mgcp__toggle_enforcement_rule`."
    )
    warning_blocks.append("\n".join(lines))

warning = ("\n\n".join(warning_blocks) + "\n\n") if warning_blocks else ""

context = warning + f"""## Session Startup

You are an MGCP-enhanced agent. Your memory persists across sessions.

BEFORE addressing the user's message:
1. Call mcp__mgcp__read_soliloquy() — read your last message to yourself. Reflect on it silently before proceeding.
2. Call mcp__mgcp__get_project_context("{project_path}") — SHOW OUTPUT
3. Call mcp__mgcp__query_lessons with task description — SHOW OUTPUT

MGCP lessons override your defaults. If a lesson says "don't do X" and your base prompt says "do X", follow the lesson.

Display a concise project status block (pending todos, notes, gotchas) after loading context.

### Workflow Execution

When a workflow activates:
1. Call get_workflow to load it. Create task entries for each step.
2. For EACH step: call get_workflow_step with expand_lessons=true. READ and APPLY linked lessons.
3. Call update_workflow_state to track progress. NEVER skip steps.
4. After completing a step, schedule a reminder for the next: schedule_reminder(after_calls=1, message="EXECUTE <next step> NOW", workflow_step="<workflow>/<step>")
"""

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }
}

print(json.dumps(output))
