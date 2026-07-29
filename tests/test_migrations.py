"""Tests for database migrations."""

import hashlib
import json
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from mgcp.migrations import (
    deduplicate_project_contexts,
    ensure_unique_project_path,
    migrate_related_ids_to_relationships,
    repair_rem_state_rows,
    run_all_migrations,
)
from mgcp.persistence import LessonStore


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    Path(f.name).unlink(missing_ok=True)


class TestDeduplicateProjectContexts:
    """Tests for the deduplicate_project_contexts migration."""

    @pytest.mark.asyncio
    async def test_no_duplicates_returns_zero(self, temp_db):
        """When no duplicates exist, returns 0."""
        # Create table with single project
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
                CREATE TABLE project_contexts (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    catalogue JSON NOT NULL DEFAULT '{}',
                    todos JSON NOT NULL DEFAULT '[]',
                    active_files JSON NOT NULL DEFAULT '[]',
                    recent_decisions JSON NOT NULL DEFAULT '[]',
                    last_session_id TEXT,
                    last_accessed TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed, session_count
                ) VALUES ('abc123', 'Test Project', '/path/to/project', '2024-01-01', 5)
            """)
            await conn.commit()

        result = await deduplicate_project_contexts(temp_db)
        assert result == 0

    @pytest.mark.asyncio
    async def test_merges_duplicates(self, temp_db):
        """Duplicates with same path are merged."""
        project_path = "/path/to/project"
        correct_id = hashlib.sha256(project_path.encode()).hexdigest()[:12]

        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
                CREATE TABLE project_contexts (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    catalogue JSON NOT NULL DEFAULT '{}',
                    todos JSON NOT NULL DEFAULT '[]',
                    active_files JSON NOT NULL DEFAULT '[]',
                    recent_decisions JSON NOT NULL DEFAULT '[]',
                    last_session_id TEXT,
                    last_accessed TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            # Insert two duplicates with same path but different IDs
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, recent_decisions, last_accessed, session_count
                ) VALUES ('old_id', 'Old Name', ?, '["decision1"]', '2024-01-01', 3)
            """, (project_path,))
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, recent_decisions, last_accessed, session_count
                ) VALUES (?, 'New Name', ?, '["decision2"]', '2024-01-02', 5)
            """, (correct_id, project_path))
            await conn.commit()

        result = await deduplicate_project_contexts(temp_db)
        assert result == 1  # One duplicate removed

        # Verify merged record
        async with aiosqlite.connect(temp_db) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM project_contexts")
            rows = await cursor.fetchall()

            assert len(rows) == 1
            row = rows[0]
            assert row["project_id"] == correct_id
            assert row["project_name"] == "New Name"  # Kept the one with higher session count
            assert row["session_count"] == 8  # 3 + 5 merged
            decisions = json.loads(row["recent_decisions"])
            assert "decision1" in decisions
            assert "decision2" in decisions

    @pytest.mark.asyncio
    async def test_handles_multiple_duplicate_paths(self, temp_db):
        """Can handle multiple different paths that each have duplicates."""
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
                CREATE TABLE project_contexts (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    catalogue JSON NOT NULL DEFAULT '{}',
                    todos JSON NOT NULL DEFAULT '[]',
                    active_files JSON NOT NULL DEFAULT '[]',
                    recent_decisions JSON NOT NULL DEFAULT '[]',
                    last_session_id TEXT,
                    last_accessed TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            # Path 1 duplicates
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed, session_count
                ) VALUES ('path1_old', 'Project 1', '/path/one', '2024-01-01', 1)
            """)
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed, session_count
                ) VALUES ('path1_new', 'Project 1 New', '/path/one', '2024-01-02', 2)
            """)
            # Path 2 duplicates
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed, session_count
                ) VALUES ('path2_old', 'Project 2', '/path/two', '2024-01-01', 3)
            """)
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed, session_count
                ) VALUES ('path2_new', 'Project 2 New', '/path/two', '2024-01-02', 4)
            """)
            await conn.commit()

        result = await deduplicate_project_contexts(temp_db)
        assert result == 2  # Two duplicates removed (one per path)

        async with aiosqlite.connect(temp_db) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM project_contexts")
            count = (await cursor.fetchone())[0]
            assert count == 2  # Two unique paths remain

    @pytest.mark.asyncio
    async def test_nonexistent_db_returns_zero(self, temp_db):
        """Nonexistent database returns 0."""
        Path(temp_db).unlink(missing_ok=True)
        result = await deduplicate_project_contexts(temp_db)
        assert result == 0


class TestEnsureUniqueProjectPath:
    """Tests for the ensure_unique_project_path migration."""

    @pytest.mark.asyncio
    async def test_adds_unique_constraint(self, temp_db):
        """Adds UNIQUE constraint when not present."""
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
                CREATE TABLE project_contexts (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    catalogue JSON NOT NULL DEFAULT '{}',
                    todos JSON NOT NULL DEFAULT '[]',
                    active_files JSON NOT NULL DEFAULT '[]',
                    recent_decisions JSON NOT NULL DEFAULT '[]',
                    last_session_id TEXT,
                    last_accessed TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed
                ) VALUES ('id1', 'Project', '/path', '2024-01-01')
            """)
            await conn.commit()

        result = await ensure_unique_project_path(temp_db)
        assert result is True

        # Verify constraint works - duplicate path should fail
        async with aiosqlite.connect(temp_db) as conn:
            with pytest.raises(aiosqlite.IntegrityError):
                await conn.execute("""
                    INSERT INTO project_contexts (
                        project_id, project_name, project_path, last_accessed
                    ) VALUES ('id2', 'Other', '/path', '2024-01-02')
                """)

    @pytest.mark.asyncio
    async def test_skips_if_constraint_exists(self, temp_db):
        """Does nothing if UNIQUE constraint already exists."""
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
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
            """)
            await conn.commit()

        result = await ensure_unique_project_path(temp_db)
        assert result is False  # No migration needed

    @pytest.mark.asyncio
    async def test_fails_with_duplicates(self, temp_db):
        """Returns False if duplicates still exist."""
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
                CREATE TABLE project_contexts (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    catalogue JSON NOT NULL DEFAULT '{}',
                    todos JSON NOT NULL DEFAULT '[]',
                    active_files JSON NOT NULL DEFAULT '[]',
                    recent_decisions JSON NOT NULL DEFAULT '[]',
                    last_session_id TEXT,
                    last_accessed TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            # Insert duplicates
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed
                ) VALUES ('id1', 'Project 1', '/path', '2024-01-01')
            """)
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed
                ) VALUES ('id2', 'Project 2', '/path', '2024-01-02')
            """)
            await conn.commit()

        result = await ensure_unique_project_path(temp_db)
        assert result is False  # Can't add constraint with duplicates

    @pytest.mark.asyncio
    async def test_nonexistent_db_returns_false(self, temp_db):
        """Nonexistent database returns False."""
        Path(temp_db).unlink(missing_ok=True)
        result = await ensure_unique_project_path(temp_db)
        assert result is False

    @pytest.mark.asyncio
    async def test_preserves_data_after_migration(self, temp_db):
        """All data is preserved after table recreation."""
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
                CREATE TABLE project_contexts (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    catalogue JSON NOT NULL DEFAULT '{}',
                    todos JSON NOT NULL DEFAULT '[]',
                    active_files JSON NOT NULL DEFAULT '[]',
                    recent_decisions JSON NOT NULL DEFAULT '[]',
                    last_session_id TEXT,
                    last_accessed TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, catalogue, todos,
                    active_files, recent_decisions, last_session_id, last_accessed,
                    session_count, notes
                ) VALUES (
                    'test_id', 'Test Project', '/test/path',
                    '{"notes": []}', '[{"content": "todo1"}]',
                    '["file1.py"]', '["decision1"]', 'session123',
                    '2024-01-01T00:00:00', 5, 'Some notes'
                )
            """)
            await conn.commit()

        await ensure_unique_project_path(temp_db)

        async with aiosqlite.connect(temp_db) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM project_contexts")
            row = await cursor.fetchone()

            assert row["project_id"] == "test_id"
            assert row["project_name"] == "Test Project"
            assert row["project_path"] == "/test/path"
            assert '"notes": []' in row["catalogue"]
            assert "todo1" in row["todos"]
            assert "file1.py" in row["active_files"]
            assert "decision1" in row["recent_decisions"]
            assert row["last_session_id"] == "session123"
            assert row["session_count"] == 5
            assert row["notes"] == "Some notes"


class TestRunAllMigrations:
    """Tests for the full migration runner."""

    @pytest.mark.asyncio
    async def test_runs_all_migrations_in_order(self, temp_db):
        """All migrations run in correct order."""
        # Set up database with duplicates and no unique constraint
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("""
                CREATE TABLE lessons (
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
                    relationships JSON NOT NULL DEFAULT '[]'
                )
            """)
            await conn.execute("""
                CREATE TABLE project_contexts (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    catalogue JSON NOT NULL DEFAULT '{}',
                    todos JSON NOT NULL DEFAULT '[]',
                    active_files JSON NOT NULL DEFAULT '[]',
                    recent_decisions JSON NOT NULL DEFAULT '[]',
                    last_session_id TEXT,
                    last_accessed TEXT NOT NULL,
                    session_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            # Insert duplicate projects
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed, session_count
                ) VALUES ('old', 'Old', '/test', '2024-01-01', 1)
            """)
            await conn.execute("""
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, last_accessed, session_count
                ) VALUES ('new', 'New', '/test', '2024-01-02', 2)
            """)
            await conn.commit()

        results = await run_all_migrations(temp_db)

        assert results["projects_deduplicated"] == 1
        assert results["unique_path_added"] is True

        # Verify final state
        async with aiosqlite.connect(temp_db) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM project_contexts")
            count = (await cursor.fetchone())[0]
            assert count == 1  # Duplicates merged


class TestMigrateRelatedIds:
    """``related_ids`` was dropped from the Lesson model; this migration is
    the only path that gets an older store's cross-links across, so it reads
    the column with raw SQL rather than through the model."""

    async def _legacy_store(self, db_path, related_ids, relationships="[]"):
        store = LessonStore(db_path)
        await store.get_all_lessons()  # the schema is created on first use
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("ALTER TABLE lessons ADD COLUMN related_ids JSON NOT NULL DEFAULT '[]'")
            await conn.execute(
                """INSERT INTO lessons (id, trigger, action, examples, tags, relationships,
                                        version, created_at, last_refined, usage_count, related_ids)
                   VALUES ('legacy', 't', 'a', '[]', '[]', ?, 1,
                           '2024-01-01T00:00:00', '2024-01-01T00:00:00', 0, ?)""",
                (relationships, json.dumps(related_ids)),
            )
            await conn.commit()
        return store

    @pytest.mark.asyncio
    async def test_legacy_column_is_folded_into_relationships(self, temp_db):
        await self._legacy_store(temp_db, ["peer-a", "peer-b"])

        assert await migrate_related_ids_to_relationships(temp_db) == 1

        lesson = await LessonStore(temp_db).get_lesson("legacy")
        assert {r.target for r in lesson.relationships} == {"peer-a", "peer-b"}
        assert all(r.type == "related" for r in lesson.relationships)

    @pytest.mark.asyncio
    async def test_ids_already_present_are_not_duplicated(self, temp_db):
        existing = json.dumps([
            {"target": "peer-a", "type": "complements", "weight": 0.9,
             "context": [], "bidirectional": True}
        ])
        await self._legacy_store(temp_db, ["peer-a"], relationships=existing)

        assert await migrate_related_ids_to_relationships(temp_db) == 0

        lesson = await LessonStore(temp_db).get_lesson("legacy")
        assert len(lesson.relationships) == 1
        assert lesson.relationships[0].type == "complements", "existing type must survive"

    @pytest.mark.asyncio
    async def test_store_without_the_column_is_a_no_op(self, temp_db):
        """A store created after the removal has no column to read, and the
        migration must not error on it -- run_all_migrations calls this on
        every open."""
        store = LessonStore(temp_db)
        await store.get_all_lessons()  # the schema is created on first use

        assert await migrate_related_ids_to_relationships(temp_db) == 0


class TestRepairRemStateRows:
    """The stored due date is the one SessionStart reads, so fixing the
    function that computes it does not fix the rows it already wrote."""

    async def _store_with_rem_state(self, db_path, rows):
        store = LessonStore(db_path)
        await store.get_all_lessons()  # the schema is created on first use
        async with aiosqlite.connect(db_path) as conn:
            for project_id, operation, last_run, next_due in rows:
                await conn.execute(
                    "INSERT INTO rem_state (project_id, operation, last_run_session, "
                    "last_run_timestamp, next_due_session) VALUES (?, ?, ?, ?, ?)",
                    (project_id, operation, last_run, "2026-07-29T00:00:00+00:00", next_due),
                )
            await conn.commit()
        return store

    async def _rows(self, db_path):
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT operation, next_due_session FROM rem_state ORDER BY operation"
            )
            return {r["operation"]: r["next_due_session"] for r in await cursor.fetchall()}

    @pytest.mark.asyncio
    async def test_early_due_dates_are_recomputed(self, temp_db):
        """staleness_scan is linear/5. Last run 98 was stored as due at 100 by
        the old interval-grid arithmetic; is_due does not fire until 103."""
        await self._store_with_rem_state(temp_db, [("p1", "staleness_scan", 98, 100)])

        assert await repair_rem_state_rows(temp_db) == 1
        assert (await self._rows(temp_db))["staleness_scan"] == 103

    @pytest.mark.asyncio
    async def test_rows_for_deleted_operations_are_dropped(self, temp_db):
        """A row for an operation that no longer exists is not inert: the
        SessionStart detector iterates rem_state without checking it against
        the operations that ship, so it would report the dead one overdue."""
        await self._store_with_rem_state(temp_db, [("p1", "action_effectiveness", 98, 100)])

        assert await repair_rem_state_rows(temp_db) == 1
        assert await self._rows(temp_db) == {}

    @pytest.mark.asyncio
    async def test_correct_rows_are_left_alone(self, temp_db):
        """Idempotent — a second run changes nothing."""
        await self._store_with_rem_state(temp_db, [("p1", "staleness_scan", 98, 103)])

        assert await repair_rem_state_rows(temp_db) == 0
        assert (await self._rows(temp_db))["staleness_scan"] == 103

    @pytest.mark.asyncio
    async def test_each_project_is_repaired_independently(self, temp_db):
        await self._store_with_rem_state(temp_db, [
            ("p1", "staleness_scan", 98, 100),
            ("p2", "staleness_scan", 10, 15),
        ])

        assert await repair_rem_state_rows(temp_db) == 1  # p2 was already right

        async with aiosqlite.connect(temp_db) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT project_id, next_due_session FROM rem_state ORDER BY project_id"
            )
            got = {r["project_id"]: r["next_due_session"] for r in await cursor.fetchall()}
        assert got == {"p1": 103, "p2": 15}

    @pytest.mark.asyncio
    async def test_missing_table_is_a_no_op(self, temp_db):
        async with aiosqlite.connect(temp_db) as conn:
            await conn.execute("CREATE TABLE placeholder (x INTEGER)")
            await conn.commit()
        assert await repair_rem_state_rows(temp_db) == 0
