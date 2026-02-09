"""Tests for version history infrastructure (REM cycle Phase A)."""

import hashlib
import json
import os
import tempfile

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
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        yield db_path


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
            "staleness_scan",
            session_number=10,
            result={"stale_count": 3},
            next_due=15,
        )

        states = await store.get_rem_state()
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

        await store.update_rem_state("staleness_scan", session_number=10)
        await store.update_rem_state("staleness_scan", session_number=20, next_due=25)

        states = await store.get_rem_state()
        assert len(states) == 1
        assert states[0]["last_run_session"] == 20
        assert states[0]["next_due_session"] == 25
