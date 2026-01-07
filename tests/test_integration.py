"""
Integration tests for MGCP.

These tests verify that multiple components work together correctly.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mgcp.graph import LessonGraph
from mgcp.models import Example, Lesson, ProjectContext, ProjectTodo, Relationship
from mgcp.persistence import LessonStore
from mgcp.vector_store import VectorStore


class TestLessonWorkflow:
    """Integration tests for lesson management workflows."""

    @pytest.fixture
    def stores(self):
        """Create all stores with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield {
                "lesson_store": LessonStore(db_path=str(Path(tmpdir) / "lessons.db")),
                "vector_store": VectorStore(persist_path=str(Path(tmpdir) / "chroma")),
                "graph": LessonGraph(),
            }

    @pytest.mark.asyncio
    async def test_add_lesson_to_all_stores(self, stores):
        """Adding a lesson updates all stores consistently."""
        lesson = Lesson(
            id="int-lesson-1",
            trigger="integration test",
            action="verify multi-store sync",
            tags=["integration", "test"]
        )

        # Add to all stores
        await stores["lesson_store"].add_lesson(lesson)
        stores["vector_store"].add_lesson(lesson)
        stores["graph"].add_lesson(lesson)

        # Verify persistence
        db_lesson = await stores["lesson_store"].get_lesson("int-lesson-1")
        assert db_lesson is not None
        assert db_lesson.trigger == "integration test"

        # Verify vector store
        vector_results = stores["vector_store"].search("integration test", limit=1)
        assert len(vector_results) > 0
        assert vector_results[0][0] == "int-lesson-1"

        # Verify graph
        assert "int-lesson-1" in stores["graph"].graph.nodes()

    @pytest.mark.asyncio
    async def test_parent_child_relationship(self, stores):
        """Parent-child relationships work across stores."""
        parent = Lesson(
            id="parent-lesson",
            trigger="parent topic",
            action="parent action"
        )
        child = Lesson(
            id="child-lesson",
            trigger="child topic",
            action="child action",
            parent_id="parent-lesson"
        )

        # Add both
        await stores["lesson_store"].add_lesson(parent)
        await stores["lesson_store"].add_lesson(child)
        stores["graph"].add_lesson(parent)
        stores["graph"].add_lesson(child)

        # Verify relationship in graph
        assert stores["graph"].get_parent("child-lesson") == "parent-lesson"
        assert "child-lesson" in stores["graph"].get_children("parent-lesson")

    @pytest.mark.asyncio
    async def test_typed_relationships(self, stores):
        """Typed relationships are properly stored and traversable."""
        lesson_a = Lesson(
            id="lesson-a",
            trigger="topic A",
            action="action A",
            relationships=[
                Relationship(target="lesson-b", type="prerequisite"),
                Relationship(target="lesson-c", type="alternative"),
            ]
        )
        lesson_b = Lesson(id="lesson-b", trigger="topic B", action="action B")
        lesson_c = Lesson(id="lesson-c", trigger="topic C", action="action C")

        # Add all
        for lesson in [lesson_a, lesson_b, lesson_c]:
            await stores["lesson_store"].add_lesson(lesson)
            stores["graph"].add_lesson(lesson)

        # Verify relationships
        prereqs = stores["graph"].get_prerequisites("lesson-a")
        assert "lesson-b" in prereqs

        alternatives = stores["graph"].get_alternatives("lesson-a")
        assert "lesson-c" in alternatives

    @pytest.mark.asyncio
    async def test_usage_tracking(self, stores):
        """Usage tracking persists correctly."""
        lesson = Lesson(
            id="usage-lesson",
            trigger="usage test",
            action="track usage"
        )

        await stores["lesson_store"].add_lesson(lesson)

        # Record multiple usages
        for _ in range(5):
            await stores["lesson_store"].record_usage("usage-lesson")

        # Verify count
        updated = await stores["lesson_store"].get_lesson("usage-lesson")
        assert updated.usage_count == 5

    @pytest.mark.asyncio
    async def test_semantic_search_finds_related(self, stores):
        """Semantic search finds semantically related lessons."""
        lessons = [
            Lesson(
                id="python-typing",
                trigger="python type hints annotations",
                action="Use type hints for better code"
            ),
            Lesson(
                id="javascript-async",
                trigger="javascript async await promises",
                action="Use async/await for async code"
            ),
            Lesson(
                id="python-async",
                trigger="python asyncio async await",
                action="Use asyncio for concurrent code"
            ),
        ]

        for lesson in lessons:
            stores["vector_store"].add_lesson(lesson)

        # Search for python-related lessons - use exact trigger text for reliable results
        results = stores["vector_store"].search("python type hints", limit=3)
        result_ids = [r[0] for r in results]

        # At minimum, we should get results
        assert len(results) > 0
        # The python-typing lesson should be found with its exact trigger
        assert "python-typing" in result_ids


class TestProjectContextWorkflow:
    """Integration tests for project context workflows."""

    @pytest.fixture
    def store(self):
        """Create a lesson store with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LessonStore(db_path=str(Path(tmpdir) / "lessons.db"))

    @pytest.mark.asyncio
    async def test_save_and_update_context(self, store):
        """Can save and update project context."""
        ctx = ProjectContext(
            project_id="proj-1",
            project_name="Test Project",
            project_path="/path/to/project",
            notes="Initial notes"
        )

        await store.save_project_context(ctx)

        # Update
        ctx.notes = "Updated notes"
        ctx.active_files = ["main.py", "utils.py"]
        await store.save_project_context(ctx)

        # Verify
        retrieved = await store.get_project_context("proj-1")
        assert retrieved.notes == "Updated notes"
        assert len(retrieved.active_files) == 2

    @pytest.mark.asyncio
    async def test_todo_management(self, store):
        """Can manage todos within project context."""
        ctx = ProjectContext(
            project_id="todo-proj",
            project_name="Todo Test",
            project_path="/path/to/todo",
            todos=[
                ProjectTodo(content="Task 1", status="pending"),
                ProjectTodo(content="Task 2", status="pending"),
            ]
        )

        await store.save_project_context(ctx)

        # Mark task as completed
        ctx.todos[0].status = "completed"
        await store.save_project_context(ctx)

        # Verify
        retrieved = await store.get_project_context("todo-proj")
        assert retrieved.todos[0].status == "completed"
        assert retrieved.todos[1].status == "pending"

    @pytest.mark.asyncio
    async def test_lookup_by_path(self, store):
        """Can lookup project context by path."""
        ctx = ProjectContext(
            project_id="path-proj",
            project_name="Path Test",
            project_path="/unique/project/path"
        )

        await store.save_project_context(ctx)

        # Lookup by path
        retrieved = await store.get_project_context_by_path("/unique/project/path")
        assert retrieved is not None
        assert retrieved.project_id == "path-proj"

    @pytest.mark.asyncio
    async def test_multiple_projects(self, store):
        """Can manage multiple projects independently."""
        projects = [
            ProjectContext(
                project_id=f"proj-{i}",
                project_name=f"Project {i}",
                project_path=f"/path/to/project-{i}"
            )
            for i in range(3)
        ]

        for proj in projects:
            await store.save_project_context(proj)

        # Verify all exist
        for i in range(3):
            retrieved = await store.get_project_context(f"proj-{i}")
            assert retrieved is not None
            assert retrieved.project_name == f"Project {i}"


class TestGraphTraversal:
    """Integration tests for graph traversal operations."""

    @pytest.fixture
    def populated_graph(self):
        """Create a graph with a complex structure."""
        graph = LessonGraph()

        # Create a tree structure with cross-links
        lessons = [
            Lesson(id="root", trigger="root", action="root action"),
            Lesson(id="child-1", trigger="child 1", action="action 1", parent_id="root"),
            Lesson(id="child-2", trigger="child 2", action="action 2", parent_id="root"),
            Lesson(
                id="grandchild-1",
                trigger="grandchild 1",
                action="action",
                parent_id="child-1",
                relationships=[Relationship(target="child-2", type="related")]
            ),
        ]

        for lesson in lessons:
            graph.add_lesson(lesson)

        return graph

    def test_spider_traversal_depth_1(self, populated_graph):
        """Spider traversal at depth 1 finds immediate neighbors."""
        visited, paths = populated_graph.spider("root", depth=1)

        assert "root" in visited
        assert "child-1" in visited
        assert "child-2" in visited
        # Grandchild should not be reached at depth 1
        assert "grandchild-1" not in visited

    def test_spider_traversal_depth_2(self, populated_graph):
        """Spider traversal at depth 2 finds grandchildren."""
        visited, paths = populated_graph.spider("root", depth=2)

        assert "root" in visited
        assert "child-1" in visited
        assert "child-2" in visited
        assert "grandchild-1" in visited

    def test_cross_link_traversal(self, populated_graph):
        """Cross-links are traversable."""
        # grandchild-1 has a related link to child-2
        related = populated_graph.get_related("grandchild-1")
        assert "child-2" in related


class TestExportImportIntegration:
    """Integration tests for export/import workflows."""

    @pytest.fixture
    def stores(self):
        """Create stores with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield {
                "db_path": str(Path(tmpdir) / "lessons.db"),
                "chroma_path": str(Path(tmpdir) / "chroma"),
                "export_path": Path(tmpdir) / "export.json",
            }

    @pytest.mark.asyncio
    async def test_export_creates_valid_json(self, stores):
        """Export creates a valid JSON file with correct structure."""
        from unittest.mock import AsyncMock, MagicMock

        from mgcp.data_ops import export_lessons

        # Create mock lessons
        lessons = [
            Lesson(
                id="export-1",
                trigger="export test 1",
                action="action 1",
                tags=["export", "test"]
            ),
            Lesson(
                id="export-2",
                trigger="export test 2",
                action="action 2",
                examples=[Example(label="good", code="code()", explanation="Works")],
                relationships=[Relationship(target="export-1", type="related")]
            ),
        ]

        # Export with mocked store
        with patch("mgcp.data_ops.LessonStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_all_lessons = AsyncMock(return_value=lessons)
            MockStore.return_value = mock_store

            await export_lessons(stores["export_path"])

        # Verify export file structure
        export_data = json.loads(stores["export_path"].read_text())
        assert export_data["lesson_count"] == 2
        assert "mgcp_version" in export_data
        assert "export_date" in export_data
        assert len(export_data["lessons"]) == 2

        # Verify lesson structure
        lesson_1 = next(l for l in export_data["lessons"] if l["id"] == "export-1")
        assert lesson_1["trigger"] == "export test 1"
        assert "export" in lesson_1["tags"]

        lesson_2 = next(l for l in export_data["lessons"] if l["id"] == "export-2")
        assert len(lesson_2["examples"]) == 1
        assert len(lesson_2["relationships"]) == 1


class TestCatalogueIntegration:
    """Integration tests for project catalogue functionality."""

    @pytest.fixture
    def store(self):
        """Create a lesson store with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LessonStore(db_path=str(Path(tmpdir) / "lessons.db"))

    @pytest.mark.asyncio
    async def test_catalogue_persists_with_context(self, store):
        """Catalogue data persists with project context."""
        from mgcp.models import (
            ArchitecturalNote,
            Convention,
            Decision,
            ProjectCatalogue,
            ProjectContext,
        )

        catalogue = ProjectCatalogue(
            architecture_notes=[
                ArchitecturalNote(title="API Design", description="RESTful conventions")
            ],
            conventions=[
                Convention(title="Snake Case", rule="Use snake_case for functions", category="naming")
            ],
            decisions=[
                Decision(
                    title="Database Choice",
                    decision="Use SQLite",
                    rationale="Simple and portable"
                )
            ]
        )

        ctx = ProjectContext(
            project_id="catalogue-proj",
            project_name="Catalogue Test",
            project_path="/path/to/catalogue",
            catalogue=catalogue
        )

        await store.save_project_context(ctx)

        # Retrieve and verify
        retrieved = await store.get_project_context("catalogue-proj")
        assert retrieved.catalogue is not None
        assert len(retrieved.catalogue.architecture_notes) == 1
        assert retrieved.catalogue.architecture_notes[0].title == "API Design"
        assert len(retrieved.catalogue.conventions) == 1
        assert len(retrieved.catalogue.decisions) == 1
