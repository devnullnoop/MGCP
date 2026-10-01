"""Tests for version history infrastructure (REM cycle Phase A)."""

import hashlib
import json

import pytest

from mgcp.models import (
    ArchitecturalNote,
    Lesson,
    ProjectCatalogue,
    ProjectContext,
    ProjectTodo,
)
from mgcp.persistence import LessonStore, _compute_catalogue_delta


@pytest.fixture
def sample_lesson():
    """Create a sample lesson for testing."""
    return Lesson(
        id="test-versioning",
        trigger="test version history",
        action="Original action text",
        rationale="Original rationale",
        tags=["test"],
    )


@pytest.fixture
def sample_project():
    """Create a sample project context."""
    project_path = "/test/project"
    return ProjectContext(
        project_id=hashlib.sha256(project_path.encode()).hexdigest()[:12],
        project_name="Test Project",
        project_path=project_path,
        session_count=1,
        notes="Initial session",
        active_files=["main.py"],
        todos=[ProjectTodo(content="Write tests", status="pending", priority=5)],
        recent_decisions=["Use pytest"],
    )


class TestCatalogueDelta:
    """Test catalogue delta computation."""

    def test_no_changes(self):
        data = json.dumps({"languages": ["Python"], "patterns_used": []})
        assert _compute_catalogue_delta(data, data) == {}

    def test_field_added(self):
        prev = json.dumps({"languages": ["Python"]})
        new = json.dumps({"languages": ["Python"], "patterns_used": ["MVC"]})
        delta = _compute_catalogue_delta(prev, new)
        assert delta == {"patterns_used": ["MVC"]}

    def test_field_changed(self):
        prev = json.dumps({"languages": ["Python"]})
        new = json.dumps({"languages": ["Python", "SQL"]})
        delta = _compute_catalogue_delta(prev, new)
        assert delta == {"languages": ["Python", "SQL"]}

    def test_field_removed(self):
        prev = json.dumps({"languages": ["Python"], "extra": "value"})
        new = json.dumps({"languages": ["Python"]})
        delta = _compute_catalogue_delta(prev, new)
        assert delta == {"extra": None}


class TestLessonVersionHistory:
    """Test that lesson refinements are captured in version history."""

    @pytest.mark.asyncio
    async def test_initial_lesson_has_no_versions(self, temp_db, sample_lesson):
        """A newly added lesson should have no version history entries."""
        store = LessonStore(temp_db)
        await store.add_lesson(sample_lesson)

        versions = await store.get_lesson_versions(sample_lesson.id)
        assert versions == []

    @pytest.mark.asyncio
    async def test_update_with_version_bump_creates_snapshot(self, temp_db, sample_lesson):
        """Updating a lesson with a higher version should snapshot the old one."""
        store = LessonStore(temp_db)
        await store.add_lesson(sample_lesson)

        # Simulate refinement: bump version and change action
        sample_lesson.action = "Refined action text"
        sample_lesson.rationale = "Original rationale\n\n[v2] Improved clarity"
        sample_lesson.version = 2
        await store.update_lesson(sample_lesson, refinement_reason="Improved clarity")

        versions = await store.get_lesson_versions(sample_lesson.id)
        assert len(versions) == 1
        assert versions[0]["version"] == 1
        assert versions[0]["action"] == "Original action text"
        assert versions[0]["refinement_reason"] == "Improved clarity"

    @pytest.mark.asyncio
    async def test_multiple_refinements_create_chain(self, temp_db, sample_lesson):
        """Multiple refinements should create a chain of version snapshots."""
        store = LessonStore(temp_db)
        await store.add_lesson(sample_lesson)

        # v1 -> v2
        sample_lesson.action = "Version 2 action"
        sample_lesson.version = 2
        await store.update_lesson(sample_lesson, refinement_reason="First refinement")

        # v2 -> v3
        sample_lesson.action = "Version 3 action"
        sample_lesson.version = 3
        await store.update_lesson(sample_lesson, refinement_reason="Second refinement")

        versions = await store.get_lesson_versions(sample_lesson.id)
        assert len(versions) == 2
        assert versions[0]["version"] == 1
        assert versions[1]["version"] == 2
        assert versions[1]["action"] == "Version 2 action"

    @pytest.mark.asyncio
    async def test_update_without_version_bump_no_snapshot(self, temp_db, sample_lesson):
        """Updating usage count (no version bump) should not create a snapshot."""
        store = LessonStore(temp_db)
        await store.add_lesson(sample_lesson)

        # Update without changing version (e.g., usage tracking)
        sample_lesson.usage_count = 5
        await store.update_lesson(sample_lesson)

        versions = await store.get_lesson_versions(sample_lesson.id)
        assert versions == []


class TestContextHistory:
    """Test that project context saves are captured in history."""

    @pytest.mark.asyncio
    async def test_save_creates_history_entry(self, temp_db, sample_project):
        """Saving project context should append to context_history."""
        store = LessonStore(temp_db)
        await store.save_project_context(sample_project)

        history = await store.get_context_history(sample_project.project_id)
        assert len(history) == 1
        assert history[0]["session_number"] == 1
        assert history[0]["notes"] == "Initial session"

    @pytest.mark.asyncio
    async def test_multiple_saves_create_multiple_entries(self, temp_db, sample_project):
        """Multiple saves should create multiple history entries."""
        store = LessonStore(temp_db)

        await store.save_project_context(sample_project)

        sample_project.session_count = 2
        sample_project.notes = "Second session"
        sample_project.active_files = ["main.py", "utils.py"]
        await store.save_project_context(sample_project)

        history = await store.get_context_history(sample_project.project_id)
        assert len(history) == 2
        # Most recent first
        assert history[0]["session_number"] == 2
        assert history[1]["session_number"] == 1

    @pytest.mark.asyncio
    async def test_catalogue_delta_recorded(self, temp_db, sample_project):
        """When catalogue changes, the delta should be recorded."""
        store = LessonStore(temp_db)

        await store.save_project_context(sample_project)

        # Change the catalogue
        sample_project.session_count = 2
        sample_project.catalogue = ProjectCatalogue(
            architecture_notes=[
                ArchitecturalNote(
                    title="Test Note",
                    description="A test architecture note",
                    category="architecture",
                )
            ]
        )
        await store.save_project_context(sample_project)

        history = await store.get_context_history(sample_project.project_id)
        # Second save should have a catalogue_delta since catalogue changed
        latest = history[0]
        assert latest["catalogue_hash"] is not None
        if latest["catalogue_delta"]:
            delta = json.loads(latest["catalogue_delta"])
            assert "architecture_notes" in delta

    @pytest.mark.asyncio
    async def test_current_context_still_returns_latest(self, temp_db, sample_project):
        """get_project_context should still return current state, not history."""
        store = LessonStore(temp_db)

        await store.save_project_context(sample_project)

        sample_project.session_count = 5
        sample_project.notes = "Much later"
        await store.save_project_context(sample_project)

        ctx = await store.get_project_context_by_path(sample_project.project_path)
        assert ctx is not None
        assert ctx.session_count == 5
        assert ctx.notes == "Much later"


class TestRemState:
    """Test REM state tracking."""

    @pytest.mark.asyncio
    async def test_update_and_get_rem_state(self, temp_db):
        store = LessonStore(temp_db)

        await store.update_rem_state(
            "proj-a",
            "staleness_scan",
            session_number=10,
            result={"stale_count": 3},
            next_due=15,
        )

        states = await store.get_rem_state("proj-a")
        assert len(states) == 1
        assert states[0]["operation"] == "staleness_scan"
        assert states[0]["last_run_session"] == 10
        assert states[0]["next_due_session"] == 15
        result = json.loads(states[0]["last_run_result"])
        assert result["stale_count"] == 3

    @pytest.mark.asyncio
    async def test_upsert_rem_state(self, temp_db):
        """Updating the same operation should overwrite."""
        store = LessonStore(temp_db)

        await store.update_rem_state("proj-a", "staleness_scan", session_number=10)
        await store.update_rem_state("proj-a", "staleness_scan", session_number=20, next_due=25)

        states = await store.get_rem_state("proj-a")
        assert len(states) == 1
        assert states[0]["last_run_session"] == 20
        assert states[0]["next_due_session"] == 25

    @pytest.mark.asyncio
    async def test_rem_state_is_per_project(self, temp_db):
        """The same operation in two projects keeps two independent cursors.

        This is the whole point of the key: with `operation TEXT PRIMARY KEY`
        the second write overwrote the first, and every project read back
        whichever one ran most recently.
        """
        store = LessonStore(temp_db)

        await store.update_rem_state("veteran", "staleness_scan", session_number=98)
        await store.update_rem_state("newcomer", "staleness_scan", session_number=3)

        veteran = await store.get_rem_state("veteran")
        newcomer = await store.get_rem_state("newcomer")

        assert len(veteran) == 1
        assert len(newcomer) == 1
        assert veteran[0]["last_run_session"] == 98
        assert newcomer[0]["last_run_session"] == 3

        # A project that has never run REM sees an empty schedule, not
        # somebody else's.
        assert await store.get_rem_state("never-run") == []


class TestRemStatePerProjectMigration:
    """Opening a pre-migration DB must re-key rem_state without losing rows."""

    @staticmethod
    def _seed_pre_migration_db(db_path: str):
        """Build a DB with the OLD `operation TEXT PRIMARY KEY` rem_state."""
        import sqlite3

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
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
            conn.execute("""
                CREATE TABLE rem_state (
                    operation TEXT PRIMARY KEY,
                    last_run_session INTEGER NOT NULL DEFAULT 0,
                    last_run_timestamp TEXT NOT NULL,
                    last_run_result JSON,
                    next_due_session INTEGER
                )
            """)
            conn.executemany(
                "INSERT INTO project_contexts "
                "(project_id, project_name, project_path, last_accessed, session_count) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    ("veteran-id", "Veteran", "/tmp/veteran", "2026-01-01T00:00:00Z", 98),
                    ("newcomer-id", "Newcomer", "/tmp/newcomer", "2026-01-02T00:00:00Z", 12),
                ],
            )
            conn.executemany(
                "INSERT INTO rem_state "
                "(operation, last_run_session, last_run_timestamp, next_due_session) "
                "VALUES (?, ?, ?, ?)",
                [
                    ("staleness_scan", 98, "2026-01-01T00:00:00Z", 100),
                    ("duplicate_detection", 98, "2026-01-01T00:00:00Z", 100),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_global_rows_go_to_the_busiest_project(self, temp_db):
        """The old scheduler used max(session_count), so those rows are the
        busiest project's history — they are handed to it, not dropped."""
        self._seed_pre_migration_db(temp_db)
        store = LessonStore(temp_db)

        veteran = await store.get_rem_state("veteran-id")
        assert {s["operation"] for s in veteran} == {
            "staleness_scan",
            "duplicate_detection",
        }
        assert all(s["last_run_session"] == 98 for s in veteran)

    @pytest.mark.asyncio
    async def test_younger_project_starts_clean(self, temp_db):
        """The newcomer never had a schedule of its own; it must not inherit
        the veteran's cursor, which is what kept it suppressed for 89 days."""
        self._seed_pre_migration_db(temp_db)
        store = LessonStore(temp_db)

        assert await store.get_rem_state("newcomer-id") == []

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, temp_db):
        """Re-opening an already-migrated DB must not rebuild or duplicate."""
        self._seed_pre_migration_db(temp_db)
        first = LessonStore(temp_db)
        await first.get_rem_state("veteran-id")
        await first.close_pool()

        second = LessonStore(temp_db)
        veteran = await second.get_rem_state("veteran-id")
        assert len(veteran) == 2

        # Writes still work against the rebuilt table.
        await second.update_rem_state("newcomer-id", "staleness_scan", session_number=12)
        assert len(await second.get_rem_state("newcomer-id")) == 1


class TestContextHistoryIsOnePerSession:
    """History rows are per SESSION, not per write.

    Appending on every save produced ~9 rows per session, which made the
    database mostly this table and made REM's `len(history) >= 10` gate and its
    "pending for N sessions" reasoning fire after one or two real sessions.
    """

    @pytest.mark.asyncio
    async def test_repeated_saves_in_one_session_keep_one_row(self, temp_db, sample_project):
        store = LessonStore(temp_db)
        for i in range(5):
            sample_project.notes = f"save {i}"
            await store.save_project_context(sample_project)

        history = await store.get_context_history(sample_project.project_id)
        assert len(history) == 1, f"expected 1 row for one session, got {len(history)}"
        assert history[0]["notes"] == "save 4", "last write of the session must win"

    @pytest.mark.asyncio
    async def test_new_session_adds_a_row(self, temp_db, sample_project):
        store = LessonStore(temp_db)
        await store.save_project_context(sample_project)
        sample_project.session_count = 2
        sample_project.notes = "next session"
        await store.save_project_context(sample_project)
        sample_project.session_count = 2
        sample_project.notes = "same session again"
        await store.save_project_context(sample_project)

        history = await store.get_context_history(sample_project.project_id)
        assert [h["session_number"] for h in history] == [2, 1]
        assert history[0]["notes"] == "same session again"

    @pytest.mark.asyncio
    async def test_migration_collapses_preexisting_duplicates(self, temp_db, sample_project):
        """An old database full of per-write rows must open and be collapsed."""
        store = LessonStore(temp_db)
        await store.save_project_context(sample_project)

        # Simulate the pre-fix shape: drop the guard, then append duplicates.
        async with store._connection(commit=True) as conn:
            await conn.execute("DROP INDEX IF EXISTS idx_context_history_session")
            for i in range(6):
                await conn.execute(
                    """
                    INSERT INTO context_history (
                        project_id, session_number, timestamp, notes,
                        active_files, todos, recent_decisions, catalogue_hash
                    ) VALUES (?, 1, ?, ?, '[]', '[]', '[]', 'h')
                    """,
                    (sample_project.project_id, f"2026-01-0{i + 1}T00:00:00+00:00", f"dup {i}"),
                )

        assert len(await store.get_context_history(sample_project.project_id)) == 7

        # Reopening runs the migration: collapse, then re-establish the index.
        fresh = LessonStore(temp_db)
        history = await fresh.get_context_history(sample_project.project_id)
        assert len(history) == 1, f"migration did not collapse duplicates: {len(history)}"
        assert history[0]["notes"] == "dup 5", "newest row per session must survive"


class TestCompareAndSwap:
    """Concurrent writes must be refused, not silently applied.

    Before v3, `lessons.version` was SELECTed but never used as a guard, so two
    sessions refining one lesson produced a lost update with nothing recorded.
    """

    @pytest.mark.asyncio
    async def test_second_writer_is_refused(self, temp_db, sample_lesson):
        from mgcp.persistence import StaleWriteError

        store = LessonStore(temp_db)
        await store.add_lesson(sample_lesson)

        first = await store.get_lesson(sample_lesson.id)
        second = await store.get_lesson(sample_lesson.id)
        assert first.version == second.version

        first.action = "first writer"
        first.version += 1
        await store.update_lesson(first, expected_version=second.version)

        second.action = "second writer clobbers"
        second.version += 1
        with pytest.raises(StaleWriteError) as caught:
            await store.update_lesson(second, expected_version=1)

        assert caught.value.expected == 1
        assert caught.value.actual == 2
        survivor = await store.get_lesson(sample_lesson.id)
        assert survivor.action == "first writer", "the refused write was applied anyway"

    @pytest.mark.asyncio
    async def test_unguarded_writes_still_apply(self, temp_db, sample_lesson):
        """link_lessons and bootstrap pass no expectation; they must not break."""
        store = LessonStore(temp_db)
        await store.add_lesson(sample_lesson)
        lesson = await store.get_lesson(sample_lesson.id)
        lesson.tags = ["added-by-an-additive-writer"]
        await store.update_lesson(lesson)
        assert (await store.get_lesson(sample_lesson.id)).tags == ["added-by-an-additive-writer"]

    @pytest.mark.asyncio
    async def test_missing_row_reports_no_row_rather_than_a_version(self, temp_db, sample_lesson):
        from mgcp.persistence import StaleWriteError

        store = LessonStore(temp_db)
        await store.add_lesson(sample_lesson)
        lesson = await store.get_lesson(sample_lesson.id)
        await store.delete_lesson(sample_lesson.id)
        lesson.version += 1
        with pytest.raises(StaleWriteError) as caught:
            await store.update_lesson(lesson, expected_version=1)
        assert caught.value.actual is None


class TestNarrowProjectWrites:
    """Two sessions editing different fields must both survive.

    `save_project_context` replaces todos, notes, catalogue, active_files and
    recent_decisions wholesale from whatever the caller read, so a session that
    only meant to change the notes discarded another session's todo. The narrow
    writers read and write one column inside a single transaction.
    """

    @staticmethod
    async def _seed(store, path="/srv/narrow"):
        await store.save_project_context(ProjectContext(
            project_id="narrow1", project_name="Narrow", project_path=path,
            notes="original",
            todos=[ProjectTodo(content="existing", status="pending")],
        ))
        return path

    @pytest.mark.asyncio
    async def test_a_todo_survives_a_concurrent_notes_edit(self, temp_db):
        store = LessonStore(temp_db)
        path = await self._seed(store)

        await store.upsert_todo(path, ProjectTodo(content="added by A", status="pending"))
        await store.set_project_notes(path, "changed by B")

        context = await store.get_project_context_by_path(path)
        assert {t.content for t in context.todos} == {"existing", "added by A"}, (
            "the notes edit discarded the concurrent todo"
        )
        assert context.notes == "changed by B"

    @pytest.mark.asyncio
    async def test_whole_context_save_still_loses_it(self, temp_db):
        """The behaviour the narrow writers exist to avoid, pinned deliberately.

        Whole-context save remains last-writer-wins. That is acceptable only
        because it is now the exception (session close), not the only tool.
        """
        store = LessonStore(temp_db)
        path = await self._seed(store, "/srv/wide")

        session_a = await store.get_project_context_by_path(path)
        session_b = await store.get_project_context_by_path(path)
        session_a.todos.append(ProjectTodo(content="added by A", status="pending"))
        await store.save_project_context(session_a)
        session_b.notes = "changed by B"
        await store.save_project_context(session_b)

        context = await store.get_project_context_by_path(path)
        assert {t.content for t in context.todos} == {"existing"}

    @pytest.mark.asyncio
    async def test_upsert_todo_updates_rather_than_duplicates(self, temp_db):
        store = LessonStore(temp_db)
        path = await self._seed(store, "/srv/dup")
        await store.upsert_todo(path, ProjectTodo(content="existing", status="completed"))
        context = await store.get_project_context_by_path(path)
        assert len(context.todos) == 1
        assert context.todos[0].status == "completed"

    @pytest.mark.asyncio
    async def test_append_decision_keeps_newest_first_and_dedupes(self, temp_db):
        store = LessonStore(temp_db)
        path = await self._seed(store, "/srv/dec")
        await store.append_decision(path, "chose sqlite")
        await store.append_decision(path, "chose qdrant")
        await store.append_decision(path, "chose sqlite")
        context = await store.get_project_context_by_path(path)
        assert context.recent_decisions[:2] == ["chose sqlite", "chose qdrant"]
        assert context.recent_decisions.count("chose sqlite") == 1

    @pytest.mark.asyncio
    async def test_narrow_write_on_an_unknown_project_returns_none(self, temp_db):
        store = LessonStore(temp_db)
        assert await store.set_project_notes("/srv/nope", "x") is None
