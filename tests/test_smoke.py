"""
Smoke tests for MGCP.

These tests verify that the system works at a basic level.
They should be fast and catch obvious breakages.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


class TestImports:
    """Verify all modules can be imported without errors."""

    def test_import_server(self):
        """Server module can be imported."""
        from mgcp import server
        assert hasattr(server, "main")

    def test_import_models(self):
        """Models module can be imported."""
        from mgcp.models import Lesson, ProjectContext
        assert Lesson is not None
        assert ProjectContext is not None

    def test_import_persistence(self):
        """Persistence module can be imported."""
        from mgcp.persistence import LessonStore
        assert LessonStore is not None

    def test_import_graph(self):
        """Graph module can be imported."""
        from mgcp.graph import LessonGraph
        assert LessonGraph is not None

    def test_import_vector_store(self):
        """Vector store module can be imported."""
        from mgcp.vector_store import VectorStore
        assert VectorStore is not None

    def test_import_data_ops(self):
        """Data ops module can be imported."""
        from mgcp.data_ops import export_lessons, find_duplicates, import_lessons
        assert export_lessons is not None
        assert import_lessons is not None
        assert find_duplicates is not None

    def test_import_init_project(self):
        """Init project module can be imported."""
        from mgcp.init_project import LLM_CLIENTS, main
        assert main is not None
        assert len(LLM_CLIENTS) >= 8  # We support 8 clients


class TestModelsSmoke:
    """Quick sanity checks for data models."""

    def test_create_lesson(self):
        """Can create a basic lesson."""
        from mgcp.models import Lesson
        lesson = Lesson(
            id="smoke-test",
            trigger="smoke test trigger",
            action="smoke test action"
        )
        assert lesson.id == "smoke-test"
        assert lesson.version == 1

    def test_create_project_context(self):
        """Can create a basic project context."""
        from mgcp.models import ProjectContext
        ctx = ProjectContext(
            project_id="smoke-proj",
            project_name="Smoke Test Project",
            project_path="/tmp/smoke"
        )
        assert ctx.project_id == "smoke-proj"

    def test_create_example(self):
        """Can create an example."""
        from mgcp.models import Example
        example = Example(
            label="good",
            code="print('hello')",
            explanation="Simple print"
        )
        assert example.label == "good"

    def test_create_relationship(self):
        """Can create a relationship."""
        from mgcp.models import Relationship
        rel = Relationship(
            target="other-lesson",
            type="prerequisite"
        )
        assert rel.target == "other-lesson"


class TestPersistenceSmoke:
    """Quick sanity checks for persistence layer."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "smoke.db"

    @pytest.mark.asyncio
    async def test_lesson_store_init(self, temp_db):
        """LessonStore can be initialized."""
        from mgcp.persistence import LessonStore
        store = LessonStore(db_path=str(temp_db))
        assert store is not None

    @pytest.mark.asyncio
    async def test_save_and_get_lesson(self, temp_db):
        """Can save and retrieve a lesson."""
        from mgcp.models import Lesson
        from mgcp.persistence import LessonStore

        store = LessonStore(db_path=str(temp_db))
        lesson = Lesson(
            id="smoke-save",
            trigger="save test",
            action="test action"
        )

        await store.add_lesson(lesson)
        retrieved = await store.get_lesson("smoke-save")

        assert retrieved is not None
        assert retrieved.id == "smoke-save"


class TestGraphSmoke:
    """Quick sanity checks for graph operations."""

    def test_graph_init(self):
        """LessonGraph can be initialized."""
        from mgcp.graph import LessonGraph
        graph = LessonGraph()
        assert graph is not None

    def test_add_lesson_to_graph(self):
        """Can add a lesson to the graph."""
        from mgcp.graph import LessonGraph
        from mgcp.models import Lesson

        graph = LessonGraph()
        lesson = Lesson(
            id="graph-smoke",
            trigger="graph test",
            action="test action"
        )
        graph.add_lesson(lesson)
        assert "graph-smoke" in graph.graph.nodes()


class TestVectorStoreSmoke:
    """Quick sanity checks for vector store."""

    @pytest.fixture
    def temp_chroma(self):
        """Create a temporary ChromaDB directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_vector_store_init(self, temp_chroma):
        """VectorStore can be initialized."""
        from mgcp.vector_store import VectorStore
        store = VectorStore(persist_path=temp_chroma)
        assert store is not None

    def test_add_and_search(self, temp_chroma):
        """Can add a lesson and search for it."""
        from mgcp.models import Lesson
        from mgcp.vector_store import VectorStore

        store = VectorStore(persist_path=temp_chroma)
        lesson = Lesson(
            id="vector-smoke",
            trigger="vector test search",
            action="find this lesson"
        )
        store.add_lesson(lesson)

        results = store.search("vector test", limit=1)
        assert len(results) > 0
        assert results[0][0] == "vector-smoke"


class TestCLISmoke:
    """Quick sanity checks for CLI tools."""

    def test_mgcp_help(self):
        """mgcp --help runs without error."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.server", "--help"],
            capture_output=True,
            text=True
        )
        # Help might not be implemented, but should not crash
        assert result.returncode in [0, 2]  # 0=success, 2=argparse error

    def test_mgcp_init_help(self):
        """mgcp-init --help runs without error."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--help"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "Usage" in result.stdout

    def test_mgcp_init_list(self):
        """mgcp-init --list shows all clients."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.init_project", "--list"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "claude-code" in result.stdout
        assert "cursor" in result.stdout

    def test_mgcp_export_help(self):
        """mgcp-export --help runs without error."""
        result = subprocess.run(
            [sys.executable, "-m", "mgcp.data_ops"],
            capture_output=True,
            text=True,
            input=""
        )
        # Will fail because no subcommand, but should show usage
        assert result.returncode in [0, 2]


class TestEndToEndSmoke:
    """Quick end-to-end workflow tests."""

    @pytest.fixture
    def temp_env(self):
        """Create temporary directories for all stores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield {
                "db": Path(tmpdir) / "lessons.db",
                "chroma": Path(tmpdir) / "chroma",
            }

    @pytest.mark.asyncio
    async def test_full_lesson_workflow(self, temp_env):
        """Can create, save, search, and retrieve a lesson."""
        from mgcp.graph import LessonGraph
        from mgcp.models import Lesson
        from mgcp.persistence import LessonStore
        from mgcp.vector_store import VectorStore

        # Initialize stores
        lesson_store = LessonStore(db_path=str(temp_env["db"]))
        vector_store = VectorStore(persist_path=str(temp_env["chroma"]))
        graph = LessonGraph()

        # Create lesson
        lesson = Lesson(
            id="e2e-smoke",
            trigger="end to end smoke test",
            action="verify full workflow",
            tags=["smoke", "e2e"]
        )

        # Add to all stores
        await lesson_store.add_lesson(lesson)
        vector_store.add_lesson(lesson)
        graph.add_lesson(lesson)

        # Verify in persistence
        retrieved = await lesson_store.get_lesson("e2e-smoke")
        assert retrieved is not None
        assert retrieved.trigger == "end to end smoke test"

        # Verify in vector store
        results = vector_store.search("smoke test workflow", limit=1)
        assert len(results) > 0
        assert results[0][0] == "e2e-smoke"

        # Verify in graph
        assert "e2e-smoke" in graph.graph.nodes()

    @pytest.mark.asyncio
    async def test_project_context_workflow(self, temp_env):
        """Can save and retrieve project context."""
        from mgcp.models import ProjectContext, ProjectTodo
        from mgcp.persistence import LessonStore

        store = LessonStore(db_path=str(temp_env["db"]))

        # Create context
        ctx = ProjectContext(
            project_id="e2e-proj",
            project_name="E2E Smoke Project",
            project_path="/tmp/e2e",
            todos=[
                ProjectTodo(content="Task 1", status="pending"),
                ProjectTodo(content="Task 2", status="completed"),
            ],
            notes="Smoke test project"
        )

        # Save and retrieve
        await store.save_project_context(ctx)
        retrieved = await store.get_project_context("e2e-proj")

        assert retrieved is not None
        assert retrieved.project_name == "E2E Smoke Project"
        assert len(retrieved.todos) == 2
