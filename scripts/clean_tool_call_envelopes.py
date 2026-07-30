#!/usr/bin/env python3
"""Report (and, on demand, repair) stored records carrying tool-call envelopes.

An agent that serialises its own tool-call wrapper into a parameter value leaves
text like `...</action>‹parameter name="rationale"›...` inside the stored
field. `models.reject_tool_call_envelope` now refuses such writes, but records
written before that guard existed are still in the store, where they pollute both
display and - for lessons - the embedding vector.

DRY RUN IS THE DEFAULT. Nothing is written without --apply, and --apply against
the live ~/.mgcp additionally requires --yes-live, because the MCP server holds a
lock on the Qdrant directory and must be stopped first.

Usage:
    # report only, against a copy
    python scripts/clean_tool_call_envelopes.py --data-dir /tmp/mgcp-copy

    # repair the copy and re-embed repaired lessons
    python scripts/clean_tool_call_envelopes.py --data-dir /tmp/mgcp-copy --apply
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mgcp.models import find_tool_call_envelope  # noqa: E402

LIVE_DATA_DIR = Path(os.path.expanduser("~/.mgcp"))

# `<parameter name="rationale">why</rationale>` - the spilled sub-field. The close
# tag is written either as the parameter's own name (what the observed corruption
# looks like) or as a generic `</parameter>`; a truncated spill has neither and
# runs to end of string.
_SPILLED_FIELD_RE = re.compile(
    r"[<‹]\s*(?:antml:)?parameter\s+name\s*=\s*[\"'](?P<name>[\w-]+)[\"']\s*[>›]"
    r"(?P<value>.*?)"
    r"(?=[<‹]\s*/\s*(?:(?P=name)|(?:antml:)?parameter)\s*[>›]|"
    r"[<‹]\s*(?:antml:)?parameter\s+name|\Z)",
    re.IGNORECASE | re.DOTALL,
)

# A stray close tag sitting immediately before the envelope - `...text</action>\n`
# - is the tail of the field the agent meant to close. Cut it too.
_TRAILING_CLOSE_RE = re.compile(r"[<‹]\s*/\s*[\w:-]+\s*[>›]\s*$")

EMPTY_VALUES = {None, "", "[]", "{}", "null"}


def split_envelope(text: str) -> tuple[str, dict[str, str]] | None:
    """Split a corrupted value into (clean_prefix, spilled_fields).

    Returns None when the text carries no envelope. The clean prefix is
    everything before the envelope begins, minus a stray trailing close tag.
    """
    match = find_tool_call_envelope(text)
    if not match:
        return None
    head, tail = text[: match.start()], text[match.start():]
    head = _TRAILING_CLOSE_RE.sub("", head).rstrip()
    spilled = {
        m.group("name"): m.group("value").strip()
        for m in _SPILLED_FIELD_RE.finditer(tail)
        if m.group("value").strip()
    }
    return head, spilled


def repair_json_leaves(obj):
    """Repair string leaves inside a decoded JSON value. Returns (obj, changed)."""
    if isinstance(obj, str):
        split = split_envelope(obj)
        return (split[0], True) if split else (obj, False)
    if isinstance(obj, list):
        results = [repair_json_leaves(item) for item in obj]
        return [r[0] for r in results], any(r[1] for r in results)
    if isinstance(obj, dict):
        results = {key: repair_json_leaves(val) for key, val in obj.items()}
        return {k: v[0] for k, v in results.items()}, any(v[1] for v in results.values())
    return obj, False


def repair_column(value: str) -> tuple[str, dict[str, str]] | None:
    """Repair one column value. Returns (new_value, spilled_fields) or None."""
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if isinstance(decoded, list | dict):
        # JSON blob (tags, recent_decisions, catalogue_delta): repair the string
        # leaves in place so the structure survives. Spilled sub-fields are not
        # recovered from inside a blob - there is no unambiguous target column.
        repaired, changed = repair_json_leaves(decoded)
        return (json.dumps(repaired), {}) if changed else None
    split = split_envelope(value)
    return split if split else None


def excerpt(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + " ..."


def diff_tail(before: str, after: str, context: int = 60) -> tuple[str, str]:
    """Render the point where the two values diverge, not their shared head.

    The repair truncates at the envelope, so a head-anchored excerpt of a 2000
    character field shows two identical lines. Anchor on the divergence instead:
    common tail-of-prefix, then the differing remainder in guillemets.
    """
    i = 0
    while i < min(len(before), len(after)) and before[i] == after[i]:
        i += 1
    head = " ".join(before[max(0, i - context):i].split())
    return (
        f"{head} «{excerpt(before[i:])}»",
        f"{head} «{excerpt(after[i:]) or '(nothing)'}»",
    )


def scan(conn: sqlite3.Connection) -> list[dict]:
    """Find every affected record and compute its proposed rewrite."""
    conn.row_factory = sqlite3.Row
    tables = [
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    findings = []
    for table in tables:
        columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        pk = columns[0]
        for row in conn.execute(f"SELECT * FROM {table}"):
            updates: dict[str, str] = {}
            recovered: dict[str, str] = {}
            unrecovered: dict[str, str] = {}
            for column in columns:
                value = row[column]
                if not isinstance(value, str):
                    continue
                result = repair_column(value)
                if result is None:
                    continue
                new_value, spilled = result
                updates[column] = new_value
                for name, spilled_value in spilled.items():
                    target = name if name in columns else None
                    current = row[target] if target else None
                    if target and target not in updates and current in EMPTY_VALUES:
                        recovered[target] = spilled_value
                    else:
                        unrecovered[name] = spilled_value
            if updates:
                findings.append(
                    {
                        "table": table,
                        "key_column": pk,
                        "key": row[pk],
                        "before": {c: row[c] for c in updates},
                        "updates": updates,
                        "recovered": recovered,
                        "unrecovered": unrecovered,
                    }
                )
    return findings


def coerce_recovered(current: str | None, value: str) -> str | None:
    """Fit a recovered value to its target column's shape, or refuse."""
    if current in ("[]", "{}"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        expected = list if current == "[]" else dict
        return json.dumps(parsed) if isinstance(parsed, expected) else None
    return value


def report(findings: list[dict]) -> None:
    for f in findings:
        print(f"\n{'=' * 78}")
        print(f"{f['table']}  [{f['key_column']}={f['key']}]")
        for column, after in f["updates"].items():
            before = f["before"][column]
            old_line, new_line = diff_tail(before, after)
            print(f"\n  {column}:  ({len(before)} chars -> {len(after)} chars)")
            print(f"    - {old_line}")
            print(f"    + {new_line}")
        for column, value in f["recovered"].items():
            print(f"\n  {column}: RECOVER from spill (column is currently empty)")
            print(f"    + {excerpt(value)}")
        for name, value in f["unrecovered"].items():
            print(f"\n  {name}: SPILLED, NOT RECOVERED (no empty target column)")
            print(f"    ! {excerpt(value)}")


def apply(conn: sqlite3.Connection, findings: list[dict]) -> dict[str, list[str]]:
    """Write the proposed rewrites.

    Returns the keys of the repaired rows that also have a vector, grouped by
    table - repairing the row is only half the job, the embedding built from the
    old text has to be rebuilt too.
    """
    repaired: dict[str, list[str]] = {"lessons": [], "project_contexts": []}
    for f in findings:
        assignments = dict(f["updates"])
        for column, value in f["recovered"].items():
            current = conn.execute(
                f"SELECT {column} FROM {f['table']} WHERE {f['key_column']} = ?",
                (f["key"],),
            ).fetchone()[0]
            coerced = coerce_recovered(current, value)
            if coerced is not None:
                assignments[column] = coerced
        clause = ", ".join(f"{c} = ?" for c in assignments)
        conn.execute(
            f"UPDATE {f['table']} SET {clause} WHERE {f['key_column']} = ?",
            (*assignments.values(), f["key"]),
        )
        if f["table"] in repaired:
            repaired[f["table"]].append(f["key"])
    conn.commit()
    return repaired


def reembed(data_dir: Path, repaired: dict[str, list[str]]) -> str:
    """Rebuild the vectors for repaired records.

    Without this the sqlite row is clean but the vector still carries the
    envelope text, so the garbage keeps ranking against every query.
    """
    lesson_ids = repaired["lessons"]
    project_ids = repaired["project_contexts"]
    if not lesson_ids and not project_ids:
        return "nothing to re-embed"
    import asyncio

    from mgcp.persistence import LessonStore
    from mgcp.qdrant_catalogue_store import QdrantCatalogueStore
    from mgcp.qdrant_vector_store import QdrantVectorStore

    store = LessonStore(str(data_dir / "lessons.db"))
    lessons, contexts = asyncio.run(_load(store, lesson_ids, project_ids))

    qdrant_path = str(data_dir / "qdrant")
    vector_store = QdrantVectorStore(persist_path=qdrant_path)
    for lesson in lessons:
        vector_store.add_lesson(lesson)
    # A catalogue item's point id is derived from its title, so a repaired title
    # would leave the old point orphaned. Drop the project's points and rebuild.
    catalogue_store = QdrantCatalogueStore(client=vector_store.client)
    items = 0
    for context in contexts:
        catalogue_store.remove_project(context.project_id)
        items += catalogue_store.index_catalogue(context.project_id, context.catalogue)
    vector_store.client.close()
    return f"{len(lessons)} lessons, {items} catalogue items across {len(contexts)} projects"


async def _load(store, lesson_ids, project_ids):
    lessons = [ls for lid in lesson_ids if (ls := await store.get_lesson(lid))]
    contexts = [ctx for pid in project_ids if (ctx := await store.get_project_context(pid))]
    # aiosqlite pool threads are non-daemon: without this the CLI hangs at exit.
    await store.close_pool()
    return lessons, contexts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(LIVE_DATA_DIR))
    parser.add_argument("--apply", action="store_true", help="write the repairs")
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="required to --apply against the live ~/.mgcp (stop the MCP server first)",
    )
    args = parser.parse_args()

    data_dir = Path(os.path.expanduser(args.data_dir)).resolve()
    db_path = data_dir / "lessons.db"
    if not db_path.exists():
        print(f"No lessons.db at {db_path}", file=sys.stderr)
        return 2
    if args.apply and data_dir == LIVE_DATA_DIR.resolve() and not args.yes_live:
        print(
            "Refusing to --apply against the live store without --yes-live.\n"
            "Stop the MCP server first (it holds the Qdrant lock), then re-run.",
            file=sys.stderr,
        )
        return 2

    mode = "ro" if not args.apply else "rw"
    conn = sqlite3.connect(f"file:{db_path}?mode={mode}", uri=True)
    findings = scan(conn)

    print(f"Store: {db_path}")
    print(f"Affected records: {len(findings)}")
    by_table: dict[str, int] = {}
    for f in findings:
        by_table[f["table"]] = by_table.get(f["table"], 0) + 1
    for table, count in sorted(by_table.items()):
        print(f"  {table}: {count}")
    report(findings)

    if not args.apply:
        print(f"\n{'=' * 78}")
        print("DRY RUN - nothing written. Re-run with --apply to write these changes.")
        return 0

    repaired = apply(conn, findings)
    conn.close()
    print(f"\nWrote {len(findings)} records.")
    print(f"Re-embedded {reembed(data_dir, repaired)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
