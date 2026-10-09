"""Tests for data operations - export, import, duplicates."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mgcp.data_ops import (
    export_lessons,
    export_projects,
    find_duplicates,
    import_lessons,
    main_export,
)
from mgcp.models import Example, Lesson, ProjectContext, ProjectTodo, Relationship

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_lessons():
    """Create sample lessons for testing."""
    return [
        Lesson(
            id="lesson-1",
            trigger="python type hints",
            action="Always use type hints in Python functions",
            rationale="Improves code readability and IDE support",
            tags=["python", "typing", "best-practices"],
            examples=[
                Example(label="good", code="def greet(name: str) -> str:", explanation="Typed")
            ],
            version=1,
            usage_count=5,
        ),
        Lesson(
            id="lesson-2",
            trigger="error handling exceptions",
            action="Use specific exception types",
            rationale="Makes debugging easier",
            tags=["python", "errors", "exceptions"],
            parent_id="lesson-1",
            relationships=[
                Relationship(target="lesson-1", type="prerequisite")
            ],
            version=2,
            usage_count=10,
        ),
        Lesson(
            id="lesson-3",
            trigger="testing pytest",
            action="Write tests for all public functions",
            rationale="Ensures code quality",
            tags=["python", "testing", "pytest"],
            version=1,
            usage_count=0,
        ),
    ]


@pytest.fixture
def sample_projects():
    """Create sample project contexts for testing."""
    return [
        ProjectContext(
            project_id="proj-1",
            project_name="Test Project",
            project_path="/path/to/project",
            todos=[
                ProjectTodo(content="Fix bug", status="completed"),
                ProjectTodo(content="Add tests", status="pending"),
            ],
            active_files=["main.py", "utils.py"],
            recent_decisions=["Use FastAPI"],
            notes="Working on v2",
        ),
        ProjectContext(
            project_id="proj-2",
            project_name="Another Project",
            project_path="/path/to/another",
            notes="Initial setup",
        ),
    ]


# =============================================================================
# Export Tests
# =============================================================================


class TestExportLessons:
    """Tests for lesson export functionality."""

    @pytest.mark.asyncio
    async def test_export_to_file(self, temp_dir, sample_lessons):
        """Test exporting lessons to a file."""
        output_path = temp_dir / "lessons.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            result = await export_lessons(output_path, include_usage=True)

        assert result["status"] == "success"
        assert result["count"] == 3
        assert output_path.exists()

        data = json.loads(output_path.read_text())
        assert data["lesson_count"] == 3
        assert len(data["lessons"]) == 3

    @pytest.mark.asyncio
    async def test_export_to_stdout(self, sample_lessons, capsys):
        """Test exporting lessons to stdout."""
        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            result = await export_lessons(None, include_usage=True)

        assert result["status"] == "success"
        assert result["count"] == 3

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["lesson_count"] == 3

    @pytest.mark.asyncio
    async def test_export_includes_all_fields(self, temp_dir, sample_lessons):
        """Test that export includes all lesson fields."""
        output_path = temp_dir / "lessons.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            await export_lessons(output_path)

        data = json.loads(output_path.read_text())
        lesson = data["lessons"][0]

        assert "id" in lesson
        assert "trigger" in lesson
        assert "action" in lesson
        assert "rationale" in lesson
        assert "examples" in lesson
        assert "tags" in lesson
        assert "version" in lesson

    @pytest.mark.asyncio
    async def test_export_without_usage(self, temp_dir, sample_lessons):
        """Test exporting without usage statistics."""
        output_path = temp_dir / "lessons.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            await export_lessons(output_path, include_usage=False)

        data = json.loads(output_path.read_text())
        lesson = data["lessons"][0]

        assert "usage_count" not in lesson
        assert "last_used" not in lesson

    @pytest.mark.asyncio
    async def test_export_with_usage(self, temp_dir, sample_lessons):
        """Test exporting with usage statistics."""
        output_path = temp_dir / "lessons.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            await export_lessons(output_path, include_usage=True)

        data = json.loads(output_path.read_text())
        lesson = data["lessons"][0]

        assert "usage_count" in lesson
        assert lesson["usage_count"] == 5

    @pytest.mark.asyncio
    async def test_export_relationships(self, temp_dir, sample_lessons):
        """Test that relationships are exported correctly."""
        output_path = temp_dir / "lessons.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            await export_lessons(output_path)

        data = json.loads(output_path.read_text())
        lesson_2 = next(l for l in data["lessons"] if l["id"] == "lesson-2")

        assert lesson_2["parent_id"] == "lesson-1"
        assert len(lesson_2["relationships"]) == 1
        assert lesson_2["relationships"][0]["target"] == "lesson-1"

    @pytest.mark.asyncio
    async def test_export_examples(self, temp_dir, sample_lessons):
        """Test that examples are exported correctly."""
        output_path = temp_dir / "lessons.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            await export_lessons(output_path)

        data = json.loads(output_path.read_text())
        lesson_1 = next(l for l in data["lessons"] if l["id"] == "lesson-1")

        assert len(lesson_1["examples"]) == 1
        assert lesson_1["examples"][0]["label"] == "good"
        assert "code" in lesson_1["examples"][0]

    @pytest.mark.asyncio
    async def test_export_empty_database(self, temp_dir):
        """Test exporting when no lessons exist."""
        output_path = temp_dir / "lessons.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            MockStore.return_value = mock_store

            result = await export_lessons(output_path)

        assert result["count"] == 0
        data = json.loads(output_path.read_text())
        assert data["lesson_count"] == 0
        assert data["lessons"] == []


class TestExportProjects:
    """Tests for project export functionality."""

    @pytest.mark.asyncio
    async def test_export_projects_to_file(self, temp_dir, sample_projects):
        """Test exporting projects to a file."""
        output_path = temp_dir / "projects.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_project_contexts = AsyncMock(return_value=sample_projects)
            MockStore.return_value = mock_store

            result = await export_projects(output_path)

        assert result["status"] == "success"
        assert result["count"] == 2

        data = json.loads(output_path.read_text())
        assert data["project_count"] == 2

    @pytest.mark.asyncio
    async def test_export_projects_includes_todos(self, temp_dir, sample_projects):
        """Test that project todos are exported."""
        output_path = temp_dir / "projects.json"

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_project_contexts = AsyncMock(return_value=sample_projects)
            MockStore.return_value = mock_store

            await export_projects(output_path)

        data = json.loads(output_path.read_text())
        proj_1 = next(p for p in data["projects"] if p["project_id"] == "proj-1")

        assert len(proj_1["todos"]) == 2
        assert proj_1["todos"][0]["content"] == "Fix bug"


# =============================================================================
# Import Tests
# =============================================================================


class TestImportLessons:
    """Tests for lesson import functionality."""

    @pytest.mark.asyncio
    async def test_import_new_lessons(self, temp_dir):
        """Test importing new lessons."""
        import_file = temp_dir / "import.json"
        import_data = {
            "mgcp_version": "1.0.0",
            "lessons": [
                {
                    "id": "new-lesson",
                    "trigger": "new trigger",
                    "action": "new action",
                    "tags": ["new"],
                }
            ],
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.add_lesson = AsyncMock()
            MockStore.return_value = mock_store

            mock_vector = MagicMock()
            MockVector.return_value = mock_vector

            result = await import_lessons(import_file, merge_strategy="skip")

        assert result["total"] == 1
        assert result["imported"] == 1
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    async def test_import_skip_duplicates(self, temp_dir, sample_lessons):
        """Test that duplicate lessons are skipped."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {"id": "lesson-1", "trigger": "python type hints", "action": "new action"}
            ]
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file, merge_strategy="skip")

        assert result["skipped"] == 1
        assert result["imported"] == 0

    @pytest.mark.asyncio
    async def test_import_overwrite_duplicates(self, temp_dir, sample_lessons):
        """Test overwriting duplicate lessons."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {"id": "lesson-1", "trigger": "python type hints", "action": "updated action"}
            ]
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            mock_store.delete_lesson = AsyncMock()
            mock_store.add_lesson = AsyncMock()
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file, merge_strategy="overwrite")

        assert result["overwritten"] == 1
        mock_store.delete_lesson.assert_called_once_with("lesson-1")

    @pytest.mark.asyncio
    async def test_import_rename_duplicates(self, temp_dir, sample_lessons):
        """Test renaming duplicate lessons."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {"id": "lesson-1", "trigger": "python type hints", "action": "duplicate action"}
            ]
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            mock_store.add_lesson = AsyncMock()
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file, merge_strategy="rename")

        assert result["renamed"] == 1
        assert result["imported"] == 1

    @pytest.mark.asyncio
    async def test_import_dry_run(self, temp_dir):
        """Test dry run mode doesn't save anything."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {"id": "dry-run-lesson", "trigger": "test", "action": "test"}
            ]
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.add_lesson = AsyncMock()
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file, dry_run=True)

        assert result["dry_run"] is True
        assert result["imported"] == 1
        mock_store.add_lesson.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_with_relationships(self, temp_dir):
        """Test importing lessons with relationships."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {
                    "id": "rel-lesson",
                    "trigger": "test",
                    "action": "test",
                    "relationships": [
                        {"target": "other", "type": "prerequisite"}
                    ],
                }
            ]
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.add_lesson = AsyncMock()
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file)

        assert result["imported"] == 1

    @pytest.mark.asyncio
    async def test_import_handles_errors(self, temp_dir):
        """Test that import handles errors gracefully."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {"id": "good-lesson", "trigger": "test", "action": "test"},
                {"id": "bad-lesson"},  # Missing required fields
            ]
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.add_lesson = AsyncMock()
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file)

        # Should have at least tried to process both
        assert result["total"] == 2
        # At least one error for the malformed lesson
        assert len(result["errors"]) >= 1 or result["imported"] == 2

    @pytest.mark.asyncio
    async def test_import_reports_non_dict_entries(self, temp_dir):
        """A lessons list of strings parses as JSON; the report must survive it."""
        import_file = temp_dir / "import.json"
        import_file.write_text(json.dumps({"lessons": ["just a string", None]}))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file)

        assert result["imported"] == 0
        assert len(result["errors"]) == 2
        assert all("unknown" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_import_of_a_projects_export_is_an_error_not_a_no_op(self, temp_dir):
        """mgcp-export projects has no import counterpart; say so instead of exit 0."""
        import_file = temp_dir / "proj.json"
        import_file.write_text(json.dumps({"projects": [{"project_id": "abc"}]}))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file)

        assert result["total"] == 0
        assert "mgcp-backup --restore" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_import_detects_trigger_duplicates(self, temp_dir, sample_lessons):
        """Test that duplicates are detected by trigger, not just ID."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {
                    "id": "different-id",
                    "trigger": "python type hints",  # Same trigger as lesson-1
                    "action": "different action",
                }
            ]
        }
        import_file.write_text(json.dumps(import_data))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file, merge_strategy="skip")

        assert result["skipped"] == 1


# =============================================================================
# Duplicate Detection Tests
# =============================================================================


class TestFindDuplicates:
    """Duplicate detection ranks trigger collisions, not semantic similarity.

    Two lessons that fire on the same words are always returned together and
    one of them is redundant by construction. The old version gated on 0.85
    cosine similarity, and two lessons carrying the same rule in different
    words scored 0.739, so they sat in the store for nine months. Lowering the
    gate would not have helped: that pair ranked 110th of 581 candidates by
    similarity. By trigger overlap it ranks 6th of 1,537.

    These use a real store rather than mocks, because the thing under test is
    arithmetic over triggers and a mock would be asserting my own sums back.
    """

    @pytest.fixture
    def store(self, temp_db):
        from mgcp.persistence import LessonStore

        return LessonStore(temp_db)

    async def _add(self, store, lesson_id, trigger):
        from mgcp.models import Lesson

        await store.add_lesson(Lesson(
            id=lesson_id, trigger=trigger, action="Do the thing", tags=["t"]))

    @pytest.mark.asyncio
    async def test_an_identical_trigger_is_reported(self, store):
        await self._add(store, "first", "git push, pushing code, push to remote")
        await self._add(store, "second", "git push, pushing code, push to remote")

        pairs = await find_duplicates(store=store)
        assert len(pairs) == 1
        assert {pairs[0]["lesson_1"]["id"], pairs[0]["lesson_2"]["id"]} == {
            "first", "second"}
        assert pairs[0]["trigger_overlap"] == 1.0

    @pytest.mark.asyncio
    async def test_the_shared_words_are_the_evidence(self, store):
        """A pair with no reason attached is not actionable."""
        await self._add(store, "first", "git commit, coauthor, attribution")
        await self._add(store, "second", "git commit, coauthor, message")

        pairs = await find_duplicates(store=store)
        assert pairs[0]["shared_words"] == ["coauthor", "commit", "git"]

    @pytest.mark.asyncio
    async def test_one_shared_word_is_not_a_collision(self, store):
        """Two one-word triggers sharing their word reach overlap 1.0.

        That is an artifact of the arithmetic, not evidence, which is why a
        reported pair has to share at least two words.
        """
        await self._add(store, "first", "deployment")
        await self._add(store, "second", "deployment")

        assert await find_duplicates(store=store) == []

    @pytest.mark.asyncio
    async def test_below_the_floor_is_not_reported(self, store):
        await self._add(store, "first", "git commit, author, signature, trailer")
        await self._add(store, "second",
                        "git commit, author, deployment, kubernetes, helm, "
                        "cluster, ingress, rollout")

        assert await find_duplicates(store=store, min_overlap=0.9) == []
        assert await find_duplicates(store=store, min_overlap=0.1)

    @pytest.mark.asyncio
    async def test_most_overlap_comes_first(self, store):
        await self._add(store, "exact-a", "alpha, beta, gamma")
        await self._add(store, "exact-b", "alpha, beta, gamma")
        await self._add(store, "partial", "alpha, beta, delta, epsilon")

        pairs = await find_duplicates(store=store, min_overlap=0.3)
        overlaps = [p["trigger_overlap"] for p in pairs]
        assert overlaps == sorted(overlaps, reverse=True)
        assert pairs[0]["trigger_overlap"] == 1.0

    @pytest.mark.asyncio
    async def test_a_lesson_never_pairs_with_itself(self, store):
        await self._add(store, "only", "git commit, coauthor, attribution")

        assert await find_duplicates(store=store) == []

    @pytest.mark.asyncio
    async def test_no_vector_store_still_reports_the_collision(self, store):
        """The decision must not depend on a store that holds a lock.

        Similarity is context for the reader. Requiring it took the Qdrant lock
        on every duplicate scan, including the ones the commit gate forces.
        """
        await self._add(store, "first", "git push, pushing code, push to remote")
        await self._add(store, "second", "git push, pushing code, push to remote")

        pairs = await find_duplicates(store=store, vector_store=None)
        assert len(pairs) == 1
        assert pairs[0]["similarity"] is None

    @pytest.mark.asyncio
    async def test_similarity_is_reported_when_a_store_is_given(self, store):
        await self._add(store, "first", "git push, pushing code, push to remote")
        await self._add(store, "second", "git push, pushing code, push to remote")

        vectors = MagicMock()
        vectors.search = MagicMock(return_value=[("second", 0.74), ("first", 1.0)])
        pairs = await find_duplicates(store=store, vector_store=vectors)
        assert pairs[0]["similarity"] == 0.74

    @pytest.mark.asyncio
    async def test_a_failing_vector_store_does_not_hide_the_collision(self, store):
        """The words already proved it. A scoring failure cannot unprove it."""
        await self._add(store, "first", "git push, pushing code, push to remote")
        await self._add(store, "second", "git push, pushing code, push to remote")

        vectors = MagicMock()
        vectors.search = MagicMock(side_effect=RuntimeError("store is locked"))
        pairs = await find_duplicates(store=store, vector_store=vectors)
        assert len(pairs) == 1
        assert pairs[0]["similarity"] is None

    @pytest.mark.asyncio
    async def test_the_limit_caps_the_report(self, store):
        for i in range(6):
            await self._add(store, f"l{i}", "alpha, beta, gamma")

        assert len(await find_duplicates(store=store, limit=3)) == 3

    @pytest.mark.asyncio
    async def test_noise_words_do_not_make_a_collision(self, store):
        """Sharing "the" and "a" is not sharing a trigger."""
        from mgcp.data_ops import trigger_overlap

        share, shared = trigger_overlap(
            "the deployment of a cluster", "the migration of a database")
        assert shared == []
        assert share == 0.0


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestDataOpsEdgeCases:
    """Edge case and error handling tests."""

    @pytest.mark.asyncio
    async def test_export_with_none_last_used(self, temp_dir):
        """Test export handles None last_used gracefully."""
        lesson = Lesson(
            id="no-last-used",
            trigger="test",
            action="test",
            # last_used defaults to None, created_at is auto-set
        )

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[lesson])
            MockStore.return_value = mock_store

            output_path = temp_dir / "lessons.json"
            result = await export_lessons(output_path)

        assert result["status"] == "success"
        data = json.loads(output_path.read_text())
        assert data["lessons"][0]["last_used"] is None
        assert data["lessons"][0]["created_at"] is not None

    @pytest.mark.asyncio
    async def test_import_empty_file(self, temp_dir):
        """Test importing a file with no lessons."""
        import_file = temp_dir / "empty.json"
        import_file.write_text(json.dumps({"lessons": []}))

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file)

        assert result["total"] == 0
        assert result["imported"] == 0

    @pytest.mark.asyncio
    async def test_import_preserves_examples(self, temp_dir):
        """Test that examples are correctly imported."""
        import_file = temp_dir / "import.json"
        import_data = {
            "lessons": [
                {
                    "id": "example-lesson",
                    "trigger": "test",
                    "action": "test",
                    "examples": [
                        {"label": "good", "code": "print('hi')", "explanation": "Simple"}
                    ],
                }
            ]
        }
        import_file.write_text(json.dumps(import_data))

        saved_lesson = None

        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])

            async def capture_save(lesson):
                nonlocal saved_lesson
                saved_lesson = lesson

            mock_store.add_lesson = AsyncMock(side_effect=capture_save)
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            await import_lessons(import_file)

        assert saved_lesson is not None
        assert len(saved_lesson.examples) == 1
        assert saved_lesson.examples[0].label == "good"


# =============================================================================
# Regression Tests
# =============================================================================


class TestDataOpsRegressions:
    """Regression tests for previously fixed bugs."""

    @pytest.mark.asyncio
    async def test_export_example_fields_correct(self, temp_dir):
        """
        Regression: Export was using e.input/e.output but model has e.label/e.code.
        """
        lesson = Lesson(
            id="example-test",
            trigger="test",
            action="test",
            examples=[
                Example(label="good", code="code()", explanation="Works")
            ],
        )

        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[lesson])
            MockStore.return_value = mock_store

            output_path = temp_dir / "lessons.json"
            await export_lessons(output_path)

        data = json.loads(output_path.read_text())
        example = data["lessons"][0]["examples"][0]

        # Correct field names
        assert "label" in example
        assert "code" in example
        assert "explanation" in example

        # Old incorrect field names should not exist
        assert "input" not in example
        assert "output" not in example

    @pytest.mark.asyncio
    async def test_vector_search_returns_tuples(self):
        """
        Regression: VectorStore.search() returns (id, score) tuples, not dicts.
        """
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.QdrantVectorStore") as MockVector,
        ):
            lesson = Lesson(id="test", trigger="test", action="test")
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[lesson])
            MockStore.return_value = mock_store

            # Correct return format: list of (id, score) tuples
            mock_vector = MagicMock()
            mock_vector.search = MagicMock(return_value=[("other-lesson", 0.90)])
            MockVector.return_value = mock_vector

            # This should not raise TypeError
            duplicates = await find_duplicates()

        assert isinstance(duplicates, list)


class TestExportCLI:
    """Tests for the mgcp-export argument handling."""

    def test_export_all_without_output_is_rejected(self, monkeypatch, capsys):
        """Both halves would print to stdout, producing unparseable JSON."""
        monkeypatch.setattr(sys, "argv", ["mgcp-export", "all"])

        with pytest.raises(SystemExit) as exc:
            main_export()

        assert exc.value.code == 2
        assert "--output is required" in capsys.readouterr().err
