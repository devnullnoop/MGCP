"""SQLite persistence layer for MGCP lessons and telemetry."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .models import (
    Example,
    Lesson,
    ProjectCatalogue,
    ProjectContext,
    ProjectTodo,
    Relationship,
    Workflow,
    WorkflowStep,
)

logger = logging.getLogger("mgcp.persistence")

DEFAULT_DB_PATH = "~/.mgcp/lessons.db"

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
    related_ids JSON NOT NULL DEFAULT '[]',
    relationships JSON NOT NULL DEFAULT '[]',
    FOREIGN KEY (parent_id) REFERENCES lessons(id)
);

CREATE INDEX IF NOT EXISTS idx_lessons_parent ON lessons(parent_id);
CREATE INDEX IF NOT EXISTS idx_lessons_usage ON lessons(usage_count DESC);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSON NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telemetry_session ON telemetry_events(session_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_type ON telemetry_events(event_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_time ON telemetry_events(timestamp);

CREATE TABLE IF NOT EXISTS lesson_usage (
    lesson_id TEXT PRIMARY KEY,
    total_retrievals INTEGER DEFAULT 0,
    last_retrieved TEXT,
    avg_score REAL DEFAULT 0.0,
    sessions_count INTEGER DEFAULT 0,
    FOREIGN KEY (lesson_id) REFERENCES lessons(id)
);

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
    notes TEXT
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

CREATE INDEX IF NOT EXISTS idx_workflow_tags ON workflows(tags);
"""


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

        # Initialize schema if needed (thread-safe)
        async with self._init_lock:
            if not self._initialized:
                logger.info(f"Initializing database at {self.db_path}")
                await conn.executescript(SCHEMA)
                await self._run_migrations(conn)
                await conn.commit()
                self._initialized = True
                logger.info("Database initialized successfully")

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

        # Migration: Add catalogue column to project_contexts
        cursor = await conn.execute("PRAGMA table_info(project_contexts)")
        project_columns = [row[1] for row in await cursor.fetchall()]
        if project_columns and "catalogue" not in project_columns:
            await conn.execute(
                "ALTER TABLE project_contexts ADD COLUMN catalogue JSON NOT NULL DEFAULT '{}'"
            )

    async def add_lesson(self, lesson: Lesson) -> str:
        """Add a new lesson, return its ID."""
        async with self._connection(commit=True) as conn:
            await conn.execute(
                """
                INSERT INTO lessons (
                    id, trigger, action, rationale, examples, version,
                    created_at, last_refined, last_used, usage_count,
                    tags, parent_id, related_ids, relationships
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(lesson.related_ids),
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

    async def get_lessons_by_tags(self, tags: list[str]) -> list[Lesson]:
        """Get lessons matching any of the given tags."""
        async with self._connection() as conn:
            # SQLite JSON query for tag matching
            placeholders = " OR ".join(
                ["EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)" for _ in tags]
            )
            cursor = await conn.execute(
                f"SELECT * FROM lessons WHERE {placeholders}", tags
            )
            rows = await cursor.fetchall()
            return [self._row_to_lesson(row) for row in rows]

    async def update_lesson(self, lesson: Lesson) -> None:
        """Update an existing lesson."""
        async with self._connection(commit=True) as conn:
            await conn.execute(
                """
                UPDATE lessons SET
                    trigger = ?, action = ?, rationale = ?, examples = ?,
                    version = ?, last_refined = ?, last_used = ?, usage_count = ?,
                    tags = ?, parent_id = ?, related_ids = ?, relationships = ?
                WHERE id = ?
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
                    json.dumps(lesson.related_ids),
                    json.dumps([rel.model_dump() for rel in lesson.relationships]),
                    lesson.id,
                ),
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
        """Delete a lesson. Returns True if deleted."""
        async with self._connection(commit=True) as conn:
            cursor = await conn.execute(
                "DELETE FROM lessons WHERE id = ?", (lesson_id,)
            )
            deleted = cursor.rowcount > 0
            if deleted:
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

        # Parse relationships (handle missing column for backwards compatibility)
        relationships = []
        try:
            relationships_data = json.loads(row["relationships"]) if row["relationships"] else []
            relationships = [Relationship(**rel) for rel in relationships_data]
        except (KeyError, TypeError):
            # Column doesn't exist yet (pre-migration)
            pass

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
            related_ids=json.loads(row["related_ids"]),
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
        """Save or update project context."""
        async with self._connection(commit=True) as conn:
            await conn.execute(
                """
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, catalogue, todos, active_files,
                    recent_decisions, last_session_id, last_accessed, session_count, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    project_name = excluded.project_name,
                    catalogue = excluded.catalogue,
                    todos = excluded.todos,
                    active_files = excluded.active_files,
                    recent_decisions = excluded.recent_decisions,
                    last_session_id = excluded.last_session_id,
                    last_accessed = excluded.last_accessed,
                    session_count = excluded.session_count,
                    notes = excluded.notes
                """,
                (
                    context.project_id,
                    context.project_name,
                    context.project_path,
                    json.dumps(context.catalogue.model_dump(mode="json")),
                    json.dumps([t.model_dump(mode="json") for t in context.todos]),
                    json.dumps(context.active_files),
                    json.dumps(context.recent_decisions),
                    context.last_session_id,
                    context.last_accessed.isoformat(),
                    context.session_count,
                    context.notes,
                ),
            )
            logger.debug(f"Saved project context: {context.project_name}")

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

        # Parse catalogue (handle missing column for backwards compatibility)
        catalogue = ProjectCatalogue()
        try:
            catalogue_data = json.loads(row["catalogue"]) if row["catalogue"] else {}
            if catalogue_data:
                catalogue = ProjectCatalogue(**catalogue_data)
        except (KeyError, TypeError):
            pass  # Column doesn't exist yet

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
    # Workflow Methods
    # =========================================================================

    async def save_workflow(self, workflow: Workflow) -> str:
        """Save or update a workflow."""
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

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow. Returns True if deleted."""
        async with self._connection(commit=True) as conn:
            cursor = await conn.execute(
                "DELETE FROM workflows WHERE id = ?", (workflow_id,)
            )
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted workflow: {workflow_id}")
            return deleted

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
