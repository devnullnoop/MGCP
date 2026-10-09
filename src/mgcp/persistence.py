"""SQLite persistence layer for MGCP lessons, projects and workflows.

Telemetry is not here: it has its own database and schema (``telemetry.py``).
"""

import asyncio
import hashlib
import json
import logging
import os
import threading
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .models import (
    CommunitySummary,
    Example,
    Lesson,
    ProjectCatalogue,
    ProjectContext,
    ProjectTodo,
    Relationship,
    Soliloquy,
    Workflow,
    WorkflowStep,
    reject_tool_call_envelope,
)
from .rem_config import DEFAULT_SCHEDULES, next_due_session

logger = logging.getLogger("mgcp.persistence")


class StaleWriteError(RuntimeError):
    """A write was refused because the row changed since it was read.

    Compare-and-swap, not locking. The alternative is what MGCP did before:
    two sessions read the same lesson, both write, and the first edit is gone
    with nothing recorded anywhere. A refused write is recoverable — re-read
    and re-apply — whereas a lost one is not even detectable after the fact.
    """

    def __init__(self, entity: str, key: str, expected: int, actual: int | None):
        self.entity = entity
        self.key = key
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{entity} {key!r} changed since it was read "
            f"(expected version {expected}, found {actual if actual is not None else 'no row'}). "
            "Re-read it, re-apply the change, and write again."
        )


def get_default_db_path() -> str:
    """Get the default database path, respecting MGCP_DATA_DIR env var."""
    data_dir = os.environ.get("MGCP_DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / "lessons.db")
    return os.path.expanduser("~/.mgcp/lessons.db")


DEFAULT_DB_PATH = get_default_db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    action TEXT NOT NULL,
    rationale TEXT,
    examples JSON NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_refined TEXT NOT NULL,
    last_used TEXT,
    usage_count INTEGER NOT NULL DEFAULT 0,
    tags JSON NOT NULL DEFAULT '[]',
    parent_id TEXT,
    relationships JSON NOT NULL DEFAULT '[]',
    FOREIGN KEY (parent_id) REFERENCES lessons(id)
);

CREATE INDEX IF NOT EXISTS idx_lessons_parent ON lessons(parent_id);
CREATE INDEX IF NOT EXISTS idx_lessons_usage ON lessons(usage_count DESC);

CREATE TABLE IF NOT EXISTS project_contexts (
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
    notes TEXT,
    -- Bumped on every content write. The compare-and-swap token for project
    -- contexts, which have no natural version the way lessons do.
    revision INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_project_path ON project_contexts(project_path);

CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    trigger TEXT NOT NULL,
    steps JSON NOT NULL DEFAULT '[]',
    tags JSON NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS community_summaries (
    community_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    member_ids JSON NOT NULL DEFAULT '[]',
    member_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lesson_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    action TEXT NOT NULL,
    rationale TEXT,
    tags JSON NOT NULL DEFAULT '[]',
    timestamp TEXT NOT NULL,
    refinement_reason TEXT,
    session_id TEXT,
    FOREIGN KEY (lesson_id) REFERENCES lessons(id)
);

CREATE INDEX IF NOT EXISTS idx_lesson_versions_lesson ON lesson_versions(lesson_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lesson_versions_unique ON lesson_versions(lesson_id, version);

CREATE TABLE IF NOT EXISTS context_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session_number INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    notes TEXT,
    active_files JSON NOT NULL DEFAULT '[]',
    todos JSON NOT NULL DEFAULT '[]',
    recent_decisions JSON NOT NULL DEFAULT '[]',
    catalogue_hash TEXT,
    catalogue_delta JSON,
    FOREIGN KEY (project_id) REFERENCES project_contexts(project_id)
);

CREATE INDEX IF NOT EXISTS idx_context_history_project ON context_history(project_id);
CREATE INDEX IF NOT EXISTS idx_context_history_time ON context_history(timestamp);
-- idx_context_history_session (UNIQUE on project_id, session_number) is created
-- in _run_migrations, NOT here: this script runs before migrations, so creating
-- it here would raise IntegrityError on any existing database that still holds
-- the pre-upsert duplicate rows and would refuse to open the store at all.

-- The REM cadence is per project, so the cursor it is compared against must be
-- too. Keyed on operation alone, one project's run recorded a last_run_session
-- that every other project inherited, and is_due() refuses anything <= that --
-- so the busiest project silently suppressed all the younger ones.
CREATE TABLE IF NOT EXISTS rem_state (
    project_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    last_run_session INTEGER NOT NULL DEFAULT 0,
    last_run_timestamp TEXT NOT NULL,
    last_run_result JSON,
    next_due_session INTEGER,
    PRIMARY KEY (project_id, operation)
);

-- The findings themselves, not just how many there were.
--
-- rem_state keeps one row per (project, operation) holding the count. For a
-- long time that was all a cycle left behind, so a run that found 105 unused
-- lessons recommended a fix for each and then discarded every one of them. The
-- next run rediscovered the same 105 and discarded them again. Nothing between
-- runs could show the list, including the dashboard.
--
-- A run REPLACES its own rows for that project and operation. These are a
-- snapshot of what is true now, not an event log: the same unused lesson found
-- in four cycles is one problem, not four.
CREATE TABLE IF NOT EXISTS rem_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    found_at_session INTEGER NOT NULL DEFAULT 0,
    found_at TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    options JSON,
    recommended INTEGER,
    metadata JSON
);

CREATE INDEX IF NOT EXISTS idx_rem_findings_scope
    ON rem_findings(project_id, operation);

CREATE TABLE IF NOT EXISTS soliloquies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    content TEXT NOT NULL,
    session_number INTEGER NOT NULL DEFAULT 0,
    mood TEXT,
    project_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_soliloquies_timestamp ON soliloquies(timestamp DESC);
"""


def _compute_catalogue_delta(prev_json: str, new_json: str) -> dict:
    """Compute a simple delta between two catalogue JSON strings.

    Returns a dict with 'changed' keys mapping to their new values.
    Only includes top-level keys that actually differ.
    """
    prev = json.loads(prev_json)
    new = json.loads(new_json)
    delta = {}
    all_keys = set(prev.keys()) | set(new.keys())
    for key in all_keys:
        old_val = prev.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            delta[key] = new_val
    return delta


async def repair_rem_state(conn: aiosqlite.Connection) -> None:
    """Recompute stored due dates, and drop rows for operations that are gone.

    Runs on every store open, because the numbers this repairs were written
    into the database by code that has since been fixed, and the stored
    number is the one that matters: the SessionStart detector reads
    ``rem_state.next_due_session`` straight out of the table and warns "REM
    Operations Overdue" when ``session_count`` reaches it.

    Two kinds of bad row:

    * **Early due dates.** ``next_due_session`` used to return the next
      multiple of the interval — a grid — while ``is_due`` measures sessions
      elapsed since the last run. A linear/5 operation last run at 98 was
      stored as due at 100 and does not actually fire until 103, so the
      warning named operations that were not due.
    * **Rows for operations that no longer exist.** The detector iterates
      rem_state and never checks it against the operations that ship, so a
      leftover row is not inert — it gets reported overdue by name.

    Called last in ``_run_migrations``, so the table exists (SCHEMA ran first)
    and is already on the (project_id, operation) key.
    """
    cursor = await conn.execute(
        "SELECT project_id, operation, last_run_session, next_due_session FROM rem_state"
    )
    rows = await cursor.fetchall()

    for project_id, operation, last_run, stored_due in rows:
        schedule = DEFAULT_SCHEDULES.get(operation)
        if schedule is None:
            await conn.execute(
                "DELETE FROM rem_state WHERE project_id = ? AND operation = ?",
                (project_id, operation),
            )
            logger.info("Dropped rem_state row for unknown operation: %s", operation)
            continue

        correct = next_due_session(schedule, last_run or 0)
        if correct != stored_due:
            await conn.execute(
                "UPDATE rem_state SET next_due_session = ? "
                "WHERE project_id = ? AND operation = ?",
                (correct, project_id, operation),
            )
            logger.info(
                "Repaired next_due for %s: %s -> %s", operation, stored_due, correct
            )


# Stores whose pooled connections must be stopped at interpreter exit.
# Each pooled aiosqlite connection owns a worker thread; a thread created
# from a non-daemon context blocks interpreter shutdown until stopped, and
# a store referenced by a module global (e.g. server._store) never gets
# garbage-collected to trigger aiosqlite's own __del__ cleanup. Non-daemon
# threads are joined BEFORE atexit handlers run, so this must use
# threading._register_atexit (the hook concurrent.futures uses), which
# fires before that join.
_live_stores: weakref.WeakSet = weakref.WeakSet()

# Every connection this process has opened, tracked independently of the store
# that opened it. Walking stores alone was not enough: it reaches only
# connections sitting in `_pool`, so a connection CHECKED OUT at exit, or one
# whose store has already been dropped from the WeakSet, kept its non-daemon
# worker thread running and `threading._shutdown` blocked on the join forever.
# Measured: `pytest tests/test_failure_recovery.py` reported "28 passed in
# 3.2s" and then never exited, with two live aiosqlite worker threads in the
# shutdown dump.
_live_connections: weakref.WeakSet = weakref.WeakSet()


def _stop_pooled_connections() -> None:
    for store in list(_live_stores):
        for conn in store._pool:
            try:
                conn.stop()  # synchronous, safe without an event loop
            except Exception:
                pass
        store._pool.clear()
    # Then anything still running that no pool is holding.
    for conn in list(_live_connections):
        try:
            conn.stop()
        except Exception:
            pass


threading._register_atexit(_stop_pooled_connections)


class LessonStore:
    """Async SQLite storage for lessons with connection pooling and transaction safety."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = Path(os.path.expanduser(db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._pool: list[aiosqlite.Connection] = []
        self._pool_lock = asyncio.Lock()
        self._max_pool_size = 5
        _live_stores.add(self)

    @asynccontextmanager
    async def _connection(self, *, commit: bool = False) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection with automatic cleanup and optional commit.

        Args:
            commit: If True, commit on success, rollback on error.

        Usage:
            async with self._connection(commit=True) as conn:
                await conn.execute(...)
        """
        conn = await self._acquire_conn()
        try:
            yield conn
            if commit:
                await conn.commit()
                logger.debug("Transaction committed")
        except Exception as e:
            if commit:
                await conn.rollback()
                logger.warning(f"Transaction rolled back due to error: {e}")
            raise
        finally:
            await self._release_conn(conn)

    async def _acquire_conn(self) -> aiosqlite.Connection:
        """Acquire a connection from pool or create new one."""
        async with self._pool_lock:
            if self._pool:
                conn = self._pool.pop()
                logger.debug("Reused connection from pool")
                return conn

        # Create new connection
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        # aiosqlite's worker thread is non-daemon, so it must be stoppable at
        # exit whether or not it is in a pool when we get there.
        _live_connections.add(conn)

        # Initialize schema if needed (thread-safe).
        #
        # Closed on failure, and this is the origin of the exit hang rather
        # than a nicety: on a corrupt or unreadable database `executescript`
        # raises, and the connection here was then neither pooled nor closed.
        # aiosqlite's worker thread is non-daemon, so that one orphan blocked
        # `threading._shutdown` forever — the process printed its result and
        # never exited. Reproduced with a 2 KB file of garbage named *.db.
        try:
            async with self._init_lock:
                if not self._initialized:
                    logger.info(f"Initializing database at {self.db_path}")
                    await conn.executescript(SCHEMA)
                    await self._run_migrations(conn)
                    await conn.commit()
                    self._initialized = True
                    logger.info("Database initialized successfully")
        except BaseException:
            await conn.close()
            _live_connections.discard(conn)
            raise

        return conn

    async def _release_conn(self, conn: aiosqlite.Connection) -> None:
        """Return connection to pool or close if pool is full."""
        async with self._pool_lock:
            if len(self._pool) < self._max_pool_size:
                self._pool.append(conn)
                logger.debug(f"Returned connection to pool (size: {len(self._pool)})")
                return

        # Pool is full, close connection
        await conn.close()
        logger.debug("Closed connection (pool full)")

    async def close_pool(self) -> None:
        """Close all pooled connections. Call on shutdown."""
        async with self._pool_lock:
            for conn in self._pool:
                await conn.close()
            count = len(self._pool)
            self._pool.clear()
            logger.info(f"Closed {count} pooled connections")

    async def _run_migrations(self, conn: aiosqlite.Connection) -> None:
        """Run database migrations for schema updates."""
        # Migration: Add relationships column to lessons
        cursor = await conn.execute("PRAGMA table_info(lessons)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "relationships" not in columns:
            await conn.execute(
                "ALTER TABLE lessons ADD COLUMN relationships JSON NOT NULL DEFAULT '[]'"
            )

        # Migration: project contexts gain a revision counter. Without one there
        # is no way to tell "I am writing the context I read" from "I am
        # overwriting someone else's", and this upsert replaces todos,
        # catalogue, notes and recent_decisions wholesale.
        cursor = await conn.execute("PRAGMA table_info(project_contexts)")
        ctx_columns = [row[1] for row in await cursor.fetchall()]
        if "revision" not in ctx_columns:
            await conn.execute(
                "ALTER TABLE project_contexts ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
            )

        # Migration: collapse context_history to one snapshot per session, then
        # enforce it. The table was appended to on every save_project_context
        # call rather than once per session, so it grew ~9 rows per session and
        # became the bulk of the database, while REM read row COUNT as a session
        # count. Keep the newest row per (project_id, session_number) — that is
        # the snapshot the readers want — and drop the rest. Idempotent: after
        # the first run there is nothing to delete and the index already exists.
        collapsed = await conn.execute(
            """
            DELETE FROM context_history
            WHERE id NOT IN (
                SELECT MAX(id) FROM context_history
                GROUP BY project_id, session_number
            )
            """
        )
        if collapsed.rowcount and collapsed.rowcount > 0:
            # Deliberately not VACUUMing here: it cannot run inside this
            # transaction, and it rewrites the whole file, which is not
            # something to do unannounced while opening a store. Say what it
            # would buy and let the operator choose the moment.
            logger.warning(
                "context_history: collapsed %d duplicate rows to one snapshot per "
                "session. The freed pages are still in the file; run "
                "'sqlite3 %s VACUUM' to reclaim them (typically a large fraction "
                "of the database).",
                collapsed.rowcount,
                self.db_path,
            )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_context_history_session
                ON context_history(project_id, session_number)
            """
        )

        # `graduated_to` is deliberately NOT created. It was Phase 8's marker
        # for lessons graduated out of the query pool — the strategy that
        # degraded reliability and was dropped. Nothing has ever read or
        # written it (0 of 241 live rows are populated), so stores that
        # already have the column simply carry an unused one.

        # Migration: Tag soliloquies with the project they were written in.
        # Storage stays global (one continuous inner voice); the tag only lets
        # the read prefer this project's own train of thought. Existing rows
        # keep project_id NULL and read as "untagged earlier session".
        cursor = await conn.execute("PRAGMA table_info(soliloquies)")
        soliloquy_columns = [row[1] for row in await cursor.fetchall()]
        if "project_id" not in soliloquy_columns:
            await conn.execute("ALTER TABLE soliloquies ADD COLUMN project_id TEXT")

        cursor = await conn.execute("PRAGMA table_info(project_contexts)")
        project_columns = [row[1] for row in await cursor.fetchall()]
        if "catalogue" not in project_columns:
            await conn.execute(
                "ALTER TABLE project_contexts ADD COLUMN catalogue JSON NOT NULL DEFAULT '{}'"
            )

        # Migration: give rem_state a project_id and re-key it on
        # (project_id, operation). This one cannot be an ALTER — the primary key
        # itself is changing — so the table is rebuilt.
        #
        # Attribution of the pre-existing rows is a judgement call, and it is
        # made here rather than left to the operator because the alternative
        # (dropping them) would make every project instantly due, including the
        # one whose runs those rows actually record. The old scheduler fed
        # itself max(session_count) across every project, so the busiest project
        # is precisely the one whose clock wrote these numbers; the rows are
        # handed to it. Every other project starts with no row at all, which is
        # the truth: REM has never been scheduled on its own clock.
        cursor = await conn.execute("PRAGMA table_info(rem_state)")
        rem_columns = [row[1] for row in await cursor.fetchall()]
        if "project_id" not in rem_columns:
            cursor = await conn.execute(
                "SELECT project_id, project_name, session_count FROM project_contexts "
                "ORDER BY session_count DESC, last_accessed DESC LIMIT 1"
            )
            owner_row = await cursor.fetchone()
            owner_id = owner_row[0] if owner_row else ""

            await conn.execute("DROP TABLE IF EXISTS rem_state_pre_project")
            await conn.execute("ALTER TABLE rem_state RENAME TO rem_state_pre_project")
            # The rename leaves no rem_state, so replaying SCHEMA recreates it
            # from the one definition there is. Spelling the CREATE out again
            # here would mean a column added to SCHEMA gives a migrated store a
            # different rem_state from a fresh one — and only on a machine that
            # had the pre-per-project layout, which is where it would never be
            # noticed. Every other statement in SCHEMA is IF NOT EXISTS.
            await conn.executescript(SCHEMA)
            await conn.execute(
                """
                INSERT INTO rem_state
                    (project_id, operation, last_run_session, last_run_timestamp,
                     last_run_result, next_due_session)
                SELECT ?, operation, last_run_session, last_run_timestamp,
                       last_run_result, next_due_session
                FROM rem_state_pre_project
                """,
                (owner_id,),
            )
            cursor = await conn.execute("SELECT COUNT(*) FROM rem_state")
            moved = (await cursor.fetchone())[0]
            await conn.execute("DROP TABLE rem_state_pre_project")
            logger.warning(
                "Re-keyed rem_state on (project_id, operation). %d schedule row(s) "
                "were global; they are attributed to '%s' (session_count %s), the "
                "project whose clock the old scheduler used. Every other project "
                "now starts its own REM schedule from zero.",
                moved,
                owner_row[1] if owner_row else "<no project>",
                owner_row[2] if owner_row else "-",
            )

        await repair_rem_state(conn)

    async def add_lesson(self, lesson: Lesson) -> str:
        """Add a new lesson, return its ID."""
        reject_tool_call_envelope(lesson)
        async with self._connection(commit=True) as conn:
            await conn.execute(
                """
                INSERT INTO lessons (
                    id, trigger, action, rationale, examples, version,
                    created_at, last_refined, last_used, usage_count,
                    tags, parent_id, relationships
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lesson.id,
                    lesson.trigger,
                    lesson.action,
                    lesson.rationale,
                    json.dumps([ex.model_dump() for ex in lesson.examples]),
                    lesson.version,
                    lesson.created_at.isoformat(),
                    lesson.last_refined.isoformat(),
                    lesson.last_used.isoformat() if lesson.last_used else None,
                    lesson.usage_count,
                    json.dumps(lesson.tags),
                    lesson.parent_id,
                    json.dumps([rel.model_dump() for rel in lesson.relationships]),
                ),
            )
            logger.debug(f"Added lesson: {lesson.id}")
            return lesson.id

    async def get_lesson(self, lesson_id: str) -> Lesson | None:
        """Get a lesson by ID."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM lessons WHERE id = ?", (lesson_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_lesson(row)

    async def get_all_lessons(self) -> list[Lesson]:
        """Get all lessons."""
        async with self._connection() as conn:
            cursor = await conn.execute("SELECT * FROM lessons ORDER BY usage_count DESC")
            rows = await cursor.fetchall()
            return [self._row_to_lesson(row) for row in rows]

    async def get_lessons_by_parent(self, parent_id: str | None) -> list[Lesson]:
        """Get lessons with a specific parent (None for root lessons)."""
        async with self._connection() as conn:
            if parent_id is None:
                cursor = await conn.execute(
                    "SELECT * FROM lessons WHERE parent_id IS NULL"
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM lessons WHERE parent_id = ?", (parent_id,)
                )
            rows = await cursor.fetchall()
            return [self._row_to_lesson(row) for row in rows]

    async def update_lesson(
        self,
        lesson: Lesson,
        refinement_reason: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        """Update an existing lesson, snapshotting previous version first.

        Args:
            expected_version: the version this edit was derived from. When
                given, the write is a compare-and-swap: it applies only if the
                stored row is still at that version, and raises
                StaleWriteError otherwise. Callers that edit content
                (refine_lesson, the web editor) pass it. Callers that make
                additive, order-independent changes (link_lessons, bootstrap)
                leave it None, because a concurrent edit does not invalidate
                adding an edge.
        """
        reject_tool_call_envelope(lesson)
        async with self._connection(commit=True) as conn:
            # Snapshot current state into lesson_versions before overwriting
            cursor = await conn.execute(
                "SELECT id, version, trigger, action, rationale, tags FROM lessons WHERE id = ?",
                (lesson.id,),
            )
            prev = await cursor.fetchone()
            if prev and prev["version"] < lesson.version:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO lesson_versions (
                        lesson_id, version, trigger, action, rationale, tags,
                        timestamp, refinement_reason, session_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prev["id"],
                        prev["version"],
                        prev["trigger"],
                        prev["action"],
                        prev["rationale"],
                        prev["tags"],
                        datetime.now(UTC).isoformat(),
                        refinement_reason,
                        None,  # session_id populated by caller if available
                    ),
                )

            where_version = "" if expected_version is None else " AND version = ?"
            cursor = await conn.execute(
                f"""
                UPDATE lessons SET
                    trigger = ?, action = ?, rationale = ?, examples = ?,
                    version = ?, last_refined = ?, last_used = ?, usage_count = ?,
                    tags = ?, parent_id = ?, relationships = ?
                WHERE id = ?{where_version}
                """,
                (
                    lesson.trigger,
                    lesson.action,
                    lesson.rationale,
                    json.dumps([ex.model_dump() for ex in lesson.examples]),
                    lesson.version,
                    lesson.last_refined.isoformat(),
                    lesson.last_used.isoformat() if lesson.last_used else None,
                    lesson.usage_count,
                    json.dumps(lesson.tags),
                    lesson.parent_id,
                    json.dumps([rel.model_dump() for rel in lesson.relationships]),
                    lesson.id,
                    *([] if expected_version is None else [expected_version]),
                ),
            )
            if expected_version is not None and cursor.rowcount == 0:
                # Nothing matched: either the row is gone or someone else wrote
                # it first. Read the current version so the error can say which.
                probe = await conn.execute(
                    "SELECT version FROM lessons WHERE id = ?", (lesson.id,)
                )
                row = await probe.fetchone()
                raise StaleWriteError(
                    "lesson", lesson.id, expected_version,
                    row["version"] if row else None,
                )
            logger.debug(f"Updated lesson: {lesson.id}")

    async def record_usage(self, lesson_id: str) -> None:
        """Record that a lesson was retrieved."""
        async with self._connection(commit=True) as conn:
            now = datetime.now(UTC).isoformat()
            await conn.execute(
                """
                UPDATE lessons SET
                    usage_count = usage_count + 1,
                    last_used = ?
                WHERE id = ?
                """,
                (now, lesson_id),
            )

    async def delete_lesson(self, lesson_id: str) -> bool:
        """Delete a lesson. Returns True if deleted.

        Also repairs references left behind: children's parent_id is nulled
        (they become roots instead of unbrowsable orphans, since the FK is
        unenforced) and relationships targeting the deleted lesson are
        stripped so the next graph rebuild does not recreate it as a ghost
        node.
        """
        async with self._connection(commit=True) as conn:
            cursor = await conn.execute(
                "DELETE FROM lessons WHERE id = ?", (lesson_id,)
            )
            deleted = cursor.rowcount > 0
            if deleted:
                await conn.execute(
                    "UPDATE lessons SET parent_id = NULL WHERE parent_id = ?",
                    (lesson_id,),
                )
                cursor = await conn.execute(
                    "SELECT id, relationships FROM lessons WHERE relationships LIKE ?",
                    (f'%"{lesson_id}"%',),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    try:
                        rels = json.loads(row["relationships"]) if row["relationships"] else []
                    except (TypeError, ValueError):
                        continue
                    kept = [r for r in rels if r.get("target") != lesson_id]
                    if len(kept) != len(rels):
                        await conn.execute(
                            "UPDATE lessons SET relationships = ? WHERE id = ?",
                            (json.dumps(kept), row["id"]),
                        )
                logger.info(f"Deleted lesson: {lesson_id}")
            return deleted

    async def get_categories(self) -> list[str]:
        """Get unique top-level categories (root lesson IDs)."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT id FROM lessons WHERE parent_id IS NULL ORDER BY usage_count DESC"
            )
            rows = await cursor.fetchall()
            return [row["id"] for row in rows]

    def _row_to_lesson(self, row: aiosqlite.Row) -> Lesson:
        """Convert database row to Lesson model."""
        examples_data = json.loads(row["examples"])
        examples = [Example(**ex) for ex in examples_data]

        # Parsed without a guard on purpose. The column is created by SCHEMA and
        # ALTERed into older stores by _run_migrations before any row is read, so
        # there is no pre-migration row to be tolerant of — and the handler that
        # used to sit here swallowed malformed JSON into an empty list, quietly
        # returning a lesson stripped of its links instead of saying so.
        relationships_data = json.loads(row["relationships"]) if row["relationships"] else []
        relationships = [Relationship(**rel) for rel in relationships_data]

        return Lesson(
            id=row["id"],
            trigger=row["trigger"],
            action=row["action"],
            rationale=row["rationale"],
            examples=examples,
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_refined=datetime.fromisoformat(row["last_refined"]),
            last_used=datetime.fromisoformat(row["last_used"]) if row["last_used"] else None,
            usage_count=row["usage_count"],
            tags=json.loads(row["tags"]),
            parent_id=row["parent_id"],
            relationships=relationships,
        )

    # =========================================================================
    # Project Context Methods
    # =========================================================================

    async def get_project_context(self, project_id: str) -> ProjectContext | None:
        """Get project context by ID."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM project_contexts WHERE project_id = ?", (project_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_project_context(row)

    async def get_project_context_by_path(self, project_path: str) -> ProjectContext | None:
        """Get project context by path."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM project_contexts WHERE project_path = ?", (project_path,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_project_context(row)

    async def save_project_context(self, context: ProjectContext) -> None:
        """Save or update project context and append to history."""
        reject_tool_call_envelope(context)
        catalogue_json = json.dumps(context.catalogue.model_dump(mode="json"))
        todos_json = json.dumps([t.model_dump(mode="json") for t in context.todos])
        active_files_json = json.dumps(context.active_files)
        decisions_json = json.dumps(context.recent_decisions)

        async with self._connection(commit=True) as conn:
            # Fetch previous catalogue hash for delta computation
            prev_hash = None
            prev_catalogue_json = None
            cursor = await conn.execute(
                "SELECT catalogue FROM project_contexts WHERE project_path = ?",
                (context.project_path,),
            )
            row = await cursor.fetchone()
            if row and row["catalogue"]:
                prev_catalogue_json = row["catalogue"]
                prev_hash = hashlib.sha256(prev_catalogue_json.encode()).hexdigest()

            # Upsert current state (existing behavior)
            await conn.execute(
                """
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, catalogue, todos, active_files,
                    recent_decisions, last_session_id, last_accessed, session_count, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_path) DO UPDATE SET
                    project_id = excluded.project_id,
                    project_name = excluded.project_name,
                    catalogue = excluded.catalogue,
                    todos = excluded.todos,
                    active_files = excluded.active_files,
                    recent_decisions = excluded.recent_decisions,
                    last_session_id = excluded.last_session_id,
                    last_accessed = excluded.last_accessed,
                    session_count = excluded.session_count,
                    notes = excluded.notes,
                    revision = project_contexts.revision + 1
                """,
                (
                    context.project_id,
                    context.project_name,
                    context.project_path,
                    catalogue_json,
                    todos_json,
                    active_files_json,
                    decisions_json,
                    context.last_session_id,
                    context.last_accessed.isoformat(),
                    context.session_count,
                    context.notes,
                ),
            )

            # Upsert this session's snapshot. Last write of a session wins,
            # which is the snapshot every reader wants; appending produced
            # ~9 rows per session and made "history length" meaningless.
            new_hash = hashlib.sha256(catalogue_json.encode()).hexdigest()
            catalogue_delta = None
            if prev_hash and prev_hash != new_hash and prev_catalogue_json:
                catalogue_delta = json.dumps(
                    _compute_catalogue_delta(prev_catalogue_json, catalogue_json)
                )

            await conn.execute(
                """
                INSERT INTO context_history (
                    project_id, session_number, timestamp, notes,
                    active_files, todos, recent_decisions,
                    catalogue_hash, catalogue_delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, session_number) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    notes = excluded.notes,
                    active_files = excluded.active_files,
                    todos = excluded.todos,
                    recent_decisions = excluded.recent_decisions,
                    catalogue_hash = excluded.catalogue_hash,
                    catalogue_delta = COALESCE(
                        excluded.catalogue_delta, context_history.catalogue_delta
                    )
                """,
                (
                    context.project_id,
                    context.session_count,
                    datetime.now(UTC).isoformat(),
                    context.notes,
                    active_files_json,
                    todos_json,
                    decisions_json,
                    new_hash,
                    catalogue_delta,
                ),
            )

            logger.debug(f"Saved project context: {context.project_name}")

    async def _mutate_project_field(
        self, project_path: str, column: str, mutate
    ) -> ProjectContext | None:
        """Read one column, transform it, write it back — in ONE transaction.

        This is the other half of concurrency safety, and the cheaper half.
        `save_project_context` replaces todos, catalogue, notes, active_files
        and recent_decisions wholesale from whatever the caller held, so two
        sessions editing unrelated parts of a project destroy each other's work
        for no reason. Reading and writing a single column inside one
        transaction removes the race rather than detecting it: SQLite
        serialises the writers, so no compare-and-swap token is needed and no
        caller has to retry.

        `mutate` receives the decoded current value and returns the new one.
        """
        if column not in {"todos", "notes", "active_files", "recent_decisions"}:
            raise ValueError(f"not a narrow-writable column: {column}")
        async with self._connection(commit=True) as conn:
            cursor = await conn.execute(
                f"SELECT {column} FROM project_contexts WHERE project_path = ?",
                (project_path,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            raw = row[column]
            current = raw if column == "notes" else json.loads(raw or "[]")
            updated = mutate(current)
            encoded = updated if column == "notes" else json.dumps(updated)
            await conn.execute(
                f"UPDATE project_contexts SET {column} = ?, "
                "last_accessed = ?, revision = revision + 1 "
                "WHERE project_path = ?",
                (encoded, datetime.now(UTC).isoformat(), project_path),
            )
        return await self.get_project_context_by_path(project_path)

    async def append_decision(
        self, project_path: str, decision: str, keep: int = 20
    ) -> ProjectContext | None:
        """Append one decision without touching todos, notes or the catalogue."""
        return await self._mutate_project_field(
            project_path,
            "recent_decisions",
            lambda current: ([decision] + [d for d in current if d != decision])[:keep],
        )

    async def set_project_notes(
        self, project_path: str, notes: str
    ) -> ProjectContext | None:
        """Replace the notes field only."""
        return await self._mutate_project_field(project_path, "notes", lambda _: notes)

    async def set_active_files(
        self, project_path: str, files: list[str]
    ) -> ProjectContext | None:
        """Replace the active-files list only."""
        return await self._mutate_project_field(
            project_path, "active_files", lambda _: list(files)
        )

    async def upsert_todo(
        self, project_path: str, todo: ProjectTodo
    ) -> ProjectContext | None:
        """Add a todo, or update one that already has the same content.

        Read-modify-write of the todos column inside one transaction, so two
        sessions adding different todos both survive — which a whole-context
        save does not guarantee, because it writes back the todo list the
        caller read however long ago.
        """
        incoming = todo.model_dump(mode="json")

        def mutate(current: list) -> list:
            for existing in current:
                if existing.get("content") == todo.content:
                    existing.update(incoming)
                    return current
            return current + [incoming]

        return await self._mutate_project_field(project_path, "todos", mutate)

    async def get_all_project_contexts(self) -> list[ProjectContext]:
        """Get all project contexts ordered by last accessed."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM project_contexts ORDER BY last_accessed DESC"
            )
            rows = await cursor.fetchall()
            return [self._row_to_project_context(row) for row in rows]

    def _row_to_project_context(self, row: aiosqlite.Row) -> ProjectContext:
        """Convert database row to ProjectContext model."""
        todos_data = json.loads(row["todos"]) if row["todos"] else []
        todos = [ProjectTodo(**t) for t in todos_data]

        # No guard here either: the column is migrated in before any read, and a
        # malformed value must raise rather than hand back an empty catalogue
        # that a later save_project_context would write over the real one.
        catalogue_data = json.loads(row["catalogue"]) if row["catalogue"] else {}
        catalogue = ProjectCatalogue(**catalogue_data) if catalogue_data else ProjectCatalogue()

        return ProjectContext(
            project_id=row["project_id"],
            project_name=row["project_name"],
            project_path=row["project_path"],
            catalogue=catalogue,
            todos=todos,
            active_files=json.loads(row["active_files"]) if row["active_files"] else [],
            recent_decisions=json.loads(row["recent_decisions"]) if row["recent_decisions"] else [],
            last_session_id=row["last_session_id"],
            last_accessed=datetime.fromisoformat(row["last_accessed"]),
            session_count=row["session_count"],
            notes=row["notes"],
        )

    # =========================================================================
    # Version History Methods (REM cycle - not used during normal sessions)
    # =========================================================================

    async def get_lesson_versions(self, lesson_id: str) -> list[dict]:
        """Get all version snapshots for a lesson, ordered oldest to newest.

        This is for REM cycle analysis and finetuning data extraction only.
        Normal lesson retrieval uses get_lesson() which returns current state.
        """
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM lesson_versions
                WHERE lesson_id = ?
                ORDER BY version ASC
                """,
                (lesson_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_context_history(
        self, project_id: str, limit: int = 50
    ) -> list[dict]:
        """Get context history snapshots for a project, most recent first.

        This is for REM cycle analysis only. Normal context retrieval uses
        get_project_context() which returns current state.
        """
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM context_history
                WHERE project_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (project_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_rem_state(self, project_id: str) -> list[dict]:
        """Get the current state of all REM operations for one project.

        ``project_id`` is required rather than defaulted: a caller that forgets
        it is exactly the bug this key exists to prevent, and a default would
        quietly restore the shared-cursor behaviour.
        """
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM rem_state WHERE project_id = ? ORDER BY operation",
                (project_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_rem_state(
        self,
        project_id: str,
        operation: str,
        session_number: int,
        result: dict | None = None,
        next_due: int | None = None,
    ) -> None:
        """Update the state of a REM operation after it runs, for one project."""
        async with self._connection(commit=True) as conn:
            await conn.execute(
                """
                INSERT INTO rem_state
                    (project_id, operation, last_run_session, last_run_timestamp,
                     last_run_result, next_due_session)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, operation) DO UPDATE SET
                    last_run_session = excluded.last_run_session,
                    last_run_timestamp = excluded.last_run_timestamp,
                    last_run_result = excluded.last_run_result,
                    next_due_session = excluded.next_due_session
                """,
                (
                    project_id,
                    operation,
                    session_number,
                    datetime.now(UTC).isoformat(),
                    json.dumps(result) if result else None,
                    next_due,
                ),
            )

    async def replace_rem_findings(
        self,
        project_id: str,
        operation: str,
        session_number: int,
        findings: list,
    ) -> int:
        """Store this run's findings for one operation, replacing the last run's.

        Replace and not append. The same unused lesson found in four cycles is
        one problem, not four, and an append would turn a corpus that is not
        improving into a growing table that looks like activity.

        Returns how many rows were written.
        """
        now = datetime.now(UTC).isoformat()
        rows = [
            (
                project_id, operation, session_number, now,
                getattr(f, "title", ""), getattr(f, "description", ""),
                json.dumps(getattr(f, "options", None) or []),
                getattr(f, "recommended", None),
                json.dumps(getattr(f, "metadata", None) or {}),
            )
            for f in findings
        ]
        async with self._connection(commit=True) as conn:
            await conn.execute(
                "DELETE FROM rem_findings WHERE project_id = ? AND operation = ?",
                (project_id, operation),
            )
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO rem_findings
                        (project_id, operation, found_at_session, found_at,
                         title, description, options, recommended, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        return len(rows)

    async def get_rem_findings(
        self, project_id: str = "", operation: str = ""
    ) -> list[dict]:
        """Stored findings, newest run first. Empty filters mean every row."""
        where, params = [], []
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if operation:
            where.append("operation = ?")
            params.append(operation)
        sql = "SELECT * FROM rem_findings"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY found_at DESC, id ASC"

        async with self._connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [
            {
                "project_id": row["project_id"],
                "operation": row["operation"],
                "found_at_session": row["found_at_session"],
                "found_at": row["found_at"],
                "title": row["title"],
                "description": row["description"],
                "options": json.loads(row["options"] or "[]"),
                "recommended": row["recommended"],
                "metadata": json.loads(row["metadata"] or "{}"),
            }
            for row in rows
        ]

    # =========================================================================
    # Workflow Methods
    # =========================================================================

    async def save_workflow(self, workflow: Workflow) -> str:
        """Save or update a workflow."""
        # create_workflow, update_workflow, add_workflow_step and
        # link_lesson_to_workflow_step all land here with free text an agent
        # typed — description, guidance, checklist, outputs — so this write
        # needs the same envelope guard as add_lesson.
        reject_tool_call_envelope(workflow)
        async with self._connection(commit=True) as conn:
            await conn.execute(
                """
                INSERT INTO workflows (
                    id, name, description, trigger, steps, tags, created_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    trigger = excluded.trigger,
                    steps = excluded.steps,
                    tags = excluded.tags,
                    version = excluded.version
                """,
                (
                    workflow.id,
                    workflow.name,
                    workflow.description,
                    workflow.trigger,
                    json.dumps([s.model_dump(mode="json") for s in workflow.steps]),
                    json.dumps(workflow.tags),
                    workflow.created_at.isoformat(),
                    workflow.version,
                ),
            )
            logger.debug(f"Saved workflow: {workflow.id}")
            return workflow.id

    async def get_workflow(self, workflow_id: str) -> Workflow | None:
        """Get a workflow by ID."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_workflow(row)

    async def get_all_workflows(self) -> list[Workflow]:
        """Get all workflows."""
        async with self._connection() as conn:
            cursor = await conn.execute("SELECT * FROM workflows ORDER BY name")
            rows = await cursor.fetchall()
            return [self._row_to_workflow(row) for row in rows]

    def _row_to_workflow(self, row: aiosqlite.Row) -> Workflow:
        """Convert database row to Workflow model."""
        steps_data = json.loads(row["steps"]) if row["steps"] else []
        steps = [WorkflowStep(**s) for s in steps_data]

        return Workflow(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            trigger=row["trigger"],
            steps=steps,
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=datetime.fromisoformat(row["created_at"]),
            version=row["version"],
        )

    # =========================================================================
    # Community Summary Methods
    # =========================================================================

    async def save_community_summary(self, summary: CommunitySummary) -> str:
        """Save or update a community summary (upsert)."""
        # The title and summary are written by the agent via
        # save_community_summary, so the same guard applies.
        reject_tool_call_envelope(summary)
        async with self._connection(commit=True) as conn:
            await conn.execute(
                """
                INSERT INTO community_summaries (
                    community_id, title, summary, member_ids,
                    member_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(community_id) DO UPDATE SET
                    title = excluded.title,
                    summary = excluded.summary,
                    member_ids = excluded.member_ids,
                    member_count = excluded.member_count,
                    updated_at = excluded.updated_at
                """,
                (
                    summary.community_id,
                    summary.title,
                    summary.summary,
                    json.dumps(summary.member_ids),
                    summary.member_count,
                    summary.created_at.isoformat(),
                    summary.updated_at.isoformat(),
                ),
            )
            logger.debug(f"Saved community summary: {summary.community_id}")
            return summary.community_id

    async def get_community_summary(self, community_id: str) -> CommunitySummary | None:
        """Get a community summary by ID."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM community_summaries WHERE community_id = ?",
                (community_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_community_summary(row)

    async def get_all_community_summaries(self) -> list[CommunitySummary]:
        """Get all community summaries."""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM community_summaries ORDER BY member_count DESC"
            )
            rows = await cursor.fetchall()
            return [self._row_to_community_summary(row) for row in rows]

    def _row_to_community_summary(self, row: aiosqlite.Row) -> CommunitySummary:
        """Convert database row to CommunitySummary model."""
        return CommunitySummary(
            community_id=row["community_id"],
            title=row["title"],
            summary=row["summary"],
            member_ids=json.loads(row["member_ids"]) if row["member_ids"] else [],
            member_count=row["member_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # =========================================================================
    # Soliloquy Methods (LLM self-reflection journal)
    # =========================================================================

    async def write_soliloquy(
        self, soliloquy: Soliloquy, project_id: str | None = None
    ) -> int:
        """Write a soliloquy entry. Returns the auto-assigned row ID.

        Args:
            soliloquy: The entry to store.
            project_id: Project the entry was written in. Storage stays global;
                this only tags the row so a later read can prefer the project's
                own entries. None when the project could not be resolved.
        """
        reject_tool_call_envelope(soliloquy)
        async with self._connection(commit=True) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO soliloquies (timestamp, content, session_number, mood, project_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    soliloquy.timestamp.isoformat(),
                    soliloquy.content,
                    soliloquy.session_number,
                    soliloquy.mood,
                    project_id,
                ),
            )
            row_id = cursor.lastrowid
            logger.info(f"Wrote soliloquy #{row_id} ({len(soliloquy.content)} chars)")
            return row_id

    async def read_latest_soliloquy(
        self, project_id: str | None = None
    ) -> tuple[Soliloquy, str | None] | None:
        """Read the most recent soliloquy, preferring this project's own.

        Storage is global — the soliloquy is one continuous inner voice — so
        the read does the scoping: this project's newest entry wins, and the
        newest entry from anywhere is the fallback when this project has none.

        Returns (entry, entry_project_id) so the caller can label a fallback
        that came from somewhere else. entry_project_id is None for rows
        written before entries were tagged.
        """
        async with self._connection() as conn:
            if project_id:
                cursor = await conn.execute(
                    "SELECT * FROM soliloquies WHERE project_id = ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (project_id,),
                )
                row = await cursor.fetchone()
                if row:
                    return self._row_to_soliloquy(row), row["project_id"]

            cursor = await conn.execute(
                "SELECT * FROM soliloquies ORDER BY timestamp DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_soliloquy(row), row["project_id"]

    async def read_soliloquies(
        self, limit: int = 10
    ) -> list[tuple[Soliloquy, str | None]]:
        """Read recent soliloquy entries, newest first, across all projects.

        Returns (entry, entry_project_id) pairs so the caller can mark which
        entries came from a different project.
        """
        async with self._connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM soliloquies ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [(self._row_to_soliloquy(row), row["project_id"]) for row in rows]

    async def count_soliloquies(self) -> int:
        """Count total soliloquy entries."""
        async with self._connection() as conn:
            cursor = await conn.execute("SELECT COUNT(*) as cnt FROM soliloquies")
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    def _row_to_soliloquy(self, row: aiosqlite.Row) -> Soliloquy:
        """Convert database row to Soliloquy model."""
        return Soliloquy(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            content=row["content"],
            session_number=row["session_number"],
            mood=row["mood"],
        )
