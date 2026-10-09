#!/usr/bin/env python3
"""SessionStart hook for MGCP.

Injects only session-start bootstrap instructions:
- Read soliloquy / get project context / query lessons
- Workflow execution discipline
- Stale hook reference detection (tells user to run mgcp-init --force)
- REM operations overdue detection (tells me to call rem_run)
- Apology-gate contest-rate spike warning (tells the human to read the audit)

This list is the SessionStart token budget, so anything appended to
``warning_blocks`` belongs in it. No version number in the header: three of
these hooks claimed v2.4 while ``hook_templates/VERSION`` said 2.13, and an
annotation nobody bumps is worse than none.

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


# The worst finding per operation, with how many that operation holds. Ordered
# by id, which is insertion order, which is the rank the operation assigned.
_FINDINGS_SQL = """
SELECT operation, COUNT(*) AS n,
       (SELECT title FROM rem_findings inner_f
        WHERE inner_f.project_id = f.project_id
          AND inner_f.operation = f.operation
        ORDER BY inner_f.id LIMIT 1) AS worst
FROM rem_findings f WHERE f.project_id = ?
GROUP BY operation ORDER BY n DESC
"""


def _read_rem_state():
    """Overdue REM operations, and the findings the last runs left behind.

    One read for both, because both answer the same question: is knowledge
    maintenance keeping up. Reads ``~/.mgcp/lessons.db`` (override with
    ``MGCP_DATA_DIR``). An operation is overdue when
    ``project_contexts.session_count >= rem_state.next_due_session``. Rows of
    both kinds are scoped by project_id: another project's schedule and
    findings say nothing about this one's, and reading them unscoped reported
    whichever project ran REM last.

    The findings half exists because detection was never the problem. A cycle
    found 105 unreachable lessons, printed a fix for each, and the list only
    existed inside that one tool response. The dashboard can now apply or
    dismiss one, which is the operator's channel; this block is the agent's,
    and it is the only one that fires without anybody opening anything.

    Stdlib sqlite3 only, opens in read-only URI mode with a 2-second timeout to
    avoid blocking the live MCP server's writer. Fails open on any error:
    missing DB, missing table, missing project row, malformed schema.
    Enforcement is a safety net, not a tripwire.

    Returns ``{"overdue": [...], "findings": [...]}``, where a finding is
    ``{operation, count, worst}``.
    """
    empty = {"overdue": [], "findings": []}
    base = os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    db_path = Path(base) / "lessons.db"
    if not db_path.exists():
        return empty
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return empty
    conn.row_factory = sqlite3.Row
    try:
        try:
            ctx_row = conn.execute(
                "SELECT project_id, session_count FROM project_contexts "
                "WHERE project_path = ?",
                (project_path,),
            ).fetchone()
        except sqlite3.Error:
            return empty
        if ctx_row is None:
            return empty
        session_count = ctx_row["session_count"] or 0
        project_id = ctx_row["project_id"]
        try:
            rows = conn.execute(
                "SELECT operation, last_run_session, next_due_session "
                "FROM rem_state WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        except sqlite3.Error:
            # Includes a pre-migration rem_state with no project_id column:
            # fail open rather than report another project's schedule as ours.
            return empty
        try:
            found = conn.execute(_FINDINGS_SQL, (project_id,)).fetchall()
        except sqlite3.Error:
            # A store written before findings were kept has no such table.
            found = []
    finally:
        conn.close()

    return {
        "overdue": _overdue_rows(rows, session_count),
        "findings": [
            {"operation": r["operation"], "count": r["n"],
             "worst": r["worst"] or ""}
            for r in found
        ],
    }


def _overdue_rows(rows, session_count):
    """The schedule rows this project has already passed."""
    return [
        {
            "operation": r["operation"],
            "last_run_session": r["last_run_session"] or 0,
            "next_due_session": r["next_due_session"],
            "session_count": session_count,
        }
        for r in rows
        if r["next_due_session"] is not None
        and session_count >= r["next_due_session"]
    ]


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

def _gate_contest_stats(window: int = 50):
    """(fires, contests) over the last `window` apology-gate audit events.

    Reads ~/.mgcp/gate_audit.jsonl (MGCP_DATA_DIR-aware). A contest is an
    adjudication with verdict "not_apology". An agent that suddenly contests
    most fires is gaming the gate; the human should see that trend at
    session start, not discover it in the log later. Fails open.
    """
    path = Path(
        os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp"))
    ) / "gate_audit.jsonl"
    if not path.exists():
        return (0, 0)
    fires = contests = 0
    try:
        lines = path.read_text().splitlines()[-window:]
        for line in lines:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("gate") == "apology" and ev.get("event") == "deny":
                fires += 1
            elif (ev.get("event") == "adjudication"
                  and ev.get("verdict") == "not_apology"):
                contests += 1
    except OSError:
        return (0, 0)
    return (fires, contests)


_fires, _contests = _gate_contest_stats()
if _contests >= 3 and _contests * 2 >= max(_fires, 1):
    warning_blocks.append(
        "## ⚠️ Apology-Gate Contest Rate\n\n"
        f"In the last window the agent contested {_contests} apology-gate "
        f"fire(s) against {_fires} denial(s). A high contest rate can mean "
        "noisy tripwires — or an agent talking its way past the gate. "
        "Review `~/.mgcp/gate_audit.jsonl`: every contest carries the "
        "flagged sentence next to the recorded reasoning."
    )

rem = _read_rem_state()
if rem["findings"] or rem["overdue"]:
    lines = ["## ⚠️ Knowledge maintenance", ""]
    if rem["findings"]:
        total = sum(f["count"] for f in rem["findings"])
        lines.append(
            f"{total} REM finding(s) are open on this project, worst first. "
            f"They are stored, so they are waiting whether or not a cycle is "
            f"due. The dashboard's REM view applies or dismisses one per row."
        )
        lines.append("")
        for f in rem["findings"]:
            lines.append(f"- `{f['operation']}`: {f['count']}, worst is {f['worst']}")
        lines.append("")
        lines.append(
            "**Act on at least the worst one.** `mcp__mgcp__rem_report` lists "
            "them. A trigger collision merges with `refine_lesson` to absorb "
            "the other trigger's words, then `delete_lesson`. A lesson "
            "retrieval never reaches needs `refine_lesson(new_trigger=...)`. "
            "An isolated one needs `link_lessons`. A finding you judge "
            "harmless is dismissed in the dashboard, which is the only other "
            "way the count falls."
        )
    if rem["overdue"]:
        lines.append("")
        lines.append(
            "Operations past their next_due_session for this project. REM has "
            "no auto-trigger, so the schedule drifts until something calls it:"
        )
        lines.append("")
        for op in rem["overdue"]:
            gap = op["session_count"] - op["next_due_session"]
            lines.append(
                f"- `{op['operation']}` last ran at session "
                f"{op['last_run_session']}, due at {op['next_due_session']}, "
                f"now at {op['session_count']}, {gap} "
                f"session{'s' if gap != 1 else ''} overdue"
            )
        lines.append("")
        lines.append(
            "Call `mcp__mgcp__rem_run` with no arguments to run every due "
            "operation now, and READ what it returns."
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
