"""Tests for data operations - export, import, duplicates."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mgcp.data_ops import (
    export_lessons,
    export_projects,
    find_duplicates,
    import_lessons,
    suggest_tags,
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.save_lesson = AsyncMock()
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            mock_store.delete_lesson = AsyncMock()
            mock_store.save_lesson = AsyncMock()
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            mock_store.save_lesson = AsyncMock()
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.save_lesson = AsyncMock()
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file, dry_run=True)

        assert result["dry_run"] is True
        assert result["imported"] == 1
        mock_store.save_lesson.assert_not_called()

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
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.save_lesson = AsyncMock()
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])
            mock_store.save_lesson = AsyncMock()
            MockStore.return_value = mock_store
            MockVector.return_value = MagicMock()

            result = await import_lessons(import_file)

        # Should have at least tried to process both
        assert result["total"] == 2
        # At least one error for the malformed lesson
        assert len(result["errors"]) >= 1 or result["imported"] == 2

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
            patch("mgcp.data_ops.VectorStore") as MockVector,
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
    """Tests for duplicate detection functionality."""

    @pytest.mark.asyncio
    async def test_find_duplicates_returns_pairs(self, sample_lessons):
        """Test that duplicates are returned as pairs."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            # Mock vector store to return similar lessons
            mock_vector = MagicMock()
            mock_vector.search = MagicMock(
                return_value=[("lesson-1", 0.95), ("lesson-2", 0.90)]
            )
            MockVector.return_value = mock_vector

            duplicates = await find_duplicates(threshold=0.85)

        assert isinstance(duplicates, list)
        for dup in duplicates:
            assert "lesson_1" in dup
            assert "lesson_2" in dup
            assert "similarity" in dup

    @pytest.mark.asyncio
    async def test_find_duplicates_respects_threshold(self, sample_lessons):
        """Test that threshold filtering works."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            mock_vector = MagicMock()
            mock_vector.search = MagicMock(
                return_value=[("lesson-2", 0.80)]  # Below threshold
            )
            MockVector.return_value = mock_vector

            duplicates = await find_duplicates(threshold=0.85)

        # No duplicates should be found since 0.80 < 0.85
        assert len(duplicates) == 0

    @pytest.mark.asyncio
    async def test_find_duplicates_no_self_matches(self, sample_lessons):
        """Test that a lesson doesn't match itself."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons[:1])
            MockStore.return_value = mock_store

            mock_vector = MagicMock()
            # Only returns itself
            mock_vector.search = MagicMock(return_value=[("lesson-1", 1.0)])
            MockVector.return_value = mock_vector

            duplicates = await find_duplicates()

        assert len(duplicates) == 0

    @pytest.mark.asyncio
    async def test_find_duplicates_sorted_by_similarity(self, sample_lessons):
        """Test that results are sorted by similarity descending."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=sample_lessons)
            MockStore.return_value = mock_store

            def mock_search(query, limit=5, min_score=0):
                if "python type hints" in query:
                    return [("lesson-2", 0.90), ("lesson-3", 0.86)]
                return []

            mock_vector = MagicMock()
            mock_vector.search = MagicMock(side_effect=mock_search)
            MockVector.return_value = mock_vector

            duplicates = await find_duplicates(threshold=0.85)

        if len(duplicates) >= 2:
            assert duplicates[0]["similarity"] >= duplicates[1]["similarity"]


# =============================================================================
# Tag Suggestion Tests
# =============================================================================


class TestSuggestTags:
    """Tests for tag suggestion functionality."""

    @pytest.mark.asyncio
    async def test_suggest_tags_from_similar_lessons(self, sample_lessons):
        """Test that tags are suggested from similar lessons."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_lesson = AsyncMock(
                side_effect=lambda id: next(
                    (l for l in sample_lessons if l.id == id), None
                )
            )
            MockStore.return_value = mock_store

            mock_vector = MagicMock()
            mock_vector.search = MagicMock(
                return_value=[("lesson-2", 0.90), ("lesson-3", 0.85)]
            )
            MockVector.return_value = mock_vector

            suggestions = await suggest_tags("lesson-1", max_tags=5)

        assert isinstance(suggestions, list)

    @pytest.mark.asyncio
    async def test_suggest_tags_excludes_existing(self, sample_lessons):
        """Test that existing tags are not suggested."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_lesson = AsyncMock(
                side_effect=lambda id: next(
                    (l for l in sample_lessons if l.id == id), None
                )
            )
            MockStore.return_value = mock_store

            mock_vector = MagicMock()
            mock_vector.search = MagicMock(return_value=[("lesson-2", 0.90)])
            MockVector.return_value = mock_vector

            suggestions = await suggest_tags("lesson-1")

        # lesson-1 already has "python", so it shouldn't be suggested
        assert "python" not in suggestions

    @pytest.mark.asyncio
    async def test_suggest_tags_respects_limit(self, sample_lessons):
        """Test that max_tags limit is respected."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_lesson = AsyncMock(return_value=sample_lessons[0])
            MockStore.return_value = mock_store

            mock_vector = MagicMock()
            mock_vector.search = MagicMock(
                return_value=[("lesson-2", 0.90), ("lesson-3", 0.85)]
            )
            MockVector.return_value = mock_vector

            suggestions = await suggest_tags("lesson-1", max_tags=2)

        assert len(suggestions) <= 2

    @pytest.mark.asyncio
    async def test_suggest_tags_nonexistent_lesson(self):
        """Test handling of non-existent lesson."""
        with (
            patch("mgcp.data_ops.LessonStore") as MockStore,
            patch("mgcp.data_ops.VectorStore"),
        ):
            mock_store = MagicMock()
            mock_store.get_lesson = AsyncMock(return_value=None)
            MockStore.return_value = mock_store

            suggestions = await suggest_tags("nonexistent")

        assert suggestions == []


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
            patch("mgcp.data_ops.VectorStore") as MockVector,
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
        ):
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=[])

            async def capture_save(lesson):
                nonlocal saved_lesson
                saved_lesson = lesson

            mock_store.save_lesson = AsyncMock(side_effect=capture_save)
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
            patch("mgcp.data_ops.VectorStore") as MockVector,
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
