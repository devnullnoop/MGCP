"""Basic tests for MGCP (Memory Graph Control Protocol)."""

import os
import tempfile

import pytest

from mgcp.graph import LessonGraph
from mgcp.models import Example, Lesson, ProjectContext, ProjectTodo, Relationship
from mgcp.persistence import LessonStore
from mgcp.vector_store import VectorStore


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        yield db_path


@pytest.fixture
def temp_chroma():
    """Create a temporary ChromaDB for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_lesson():
    """Create a sample lesson for testing."""
    return Lesson(
        id="test-lesson",
        trigger="test, example, sample",
        action="This is a test action",
        rationale="For testing purposes",
        tags=["test", "sample"],
        examples=[
            Example(label="good", code="good_code()", explanation="This is good"),
            Example(label="bad", code="bad_code()", explanation="This is bad"),
        ],
    )


class TestLesson:
    """Test Lesson model."""

    def test_create_lesson(self, sample_lesson):
        """Test lesson creation."""
        assert sample_lesson.id == "test-lesson"
        assert sample_lesson.version == 1
        assert len(sample_lesson.examples) == 2

    def test_lesson_to_context(self, sample_lesson):
        """Test lesson context formatting."""
        context = sample_lesson.to_context()
        assert "test-lesson" in context
        assert "This is a test action" in context


class TestLessonStore:
    """Test persistence layer."""

    @pytest.mark.asyncio
    async def test_add_and_get_lesson(self, temp_db, sample_lesson):
        """Test adding and retrieving a lesson."""
        store = LessonStore(db_path=temp_db)

        # Add lesson
        lesson_id = await store.add_lesson(sample_lesson)
        assert lesson_id == "test-lesson"

        # Get lesson
        retrieved = await store.get_lesson("test-lesson")
        assert retrieved is not None
        assert retrieved.id == sample_lesson.id
        assert retrieved.action == sample_lesson.action

    @pytest.mark.asyncio
    async def test_lesson_not_found(self, temp_db):
        """Test getting non-existent lesson."""
        store = LessonStore(db_path=temp_db)
        result = await store.get_lesson("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_usage(self, temp_db, sample_lesson):
        """Test recording lesson usage."""
        store = LessonStore(db_path=temp_db)
        await store.add_lesson(sample_lesson)

        # Record usage
        await store.record_usage("test-lesson")
        await store.record_usage("test-lesson")

        # Check count
        lesson = await store.get_lesson("test-lesson")
        assert lesson.usage_count == 2


class TestLessonGraph:
    """Test graph operations."""

    def test_add_lesson_to_graph(self, sample_lesson):
        """Test adding a lesson to the graph."""
        graph = LessonGraph()
        graph.add_lesson(sample_lesson)

        assert "test-lesson" in graph.graph.nodes()

    def test_parent_child_relationship(self):
        """Test parent-child relationships."""
        graph = LessonGraph()

        parent = Lesson(
            id="parent",
            trigger="parent trigger",
            action="Parent action",
        )
        child = Lesson(
            id="child",
            trigger="child trigger",
            action="Child action",
            parent_id="parent",
        )

        graph.add_lesson(parent)
        graph.add_lesson(child)

        assert graph.get_parent("child") == "parent"
        assert "child" in graph.get_children("parent")

    def test_spider_traversal(self):
        """Test graph traversal."""
        graph = LessonGraph()

        # Create a small graph
        root = Lesson(id="root", trigger="root", action="Root")
        child1 = Lesson(id="child1", trigger="c1", action="C1", parent_id="root")
        child2 = Lesson(id="child2", trigger="c2", action="C2", parent_id="root")

        graph.add_lesson(root)
        graph.add_lesson(child1)
        graph.add_lesson(child2)

        visited, paths = graph.spider("root", depth=1)
        assert "root" in visited
        assert "child1" in visited
        assert "child2" in visited


class TestVectorStore:
    """Test vector store operations."""

    def test_add_and_search(self, temp_chroma, sample_lesson):
        """Test adding and searching lessons."""
        store = VectorStore(persist_path=temp_chroma)
        store.add_lesson(sample_lesson)

        # Search
        results = store.search("test example", limit=5)
        assert len(results) > 0
        assert results[0][0] == "test-lesson"

    def test_remove_lesson(self, temp_chroma, sample_lesson):
        """Test removing a lesson."""
        store = VectorStore(persist_path=temp_chroma)
        store.add_lesson(sample_lesson)

        # Remove
        store.remove_lesson("test-lesson")

        # Verify removed
        assert "test-lesson" not in store.get_all_ids()


class TestTypedRelationships:
    """Test typed relationship functionality."""

    def test_create_relationship(self):
        """Test creating a typed relationship."""
        rel = Relationship(
            target="other-lesson",
            type="prerequisite",
            weight=0.8,
            context=["ui", "debugging"],
            bidirectional=True,
        )
        assert rel.target == "other-lesson"
        assert rel.type == "prerequisite"
        assert rel.weight == 0.8
        assert "ui" in rel.context

    def test_lesson_with_relationships(self):
        """Test lesson with typed relationships."""
        lesson = Lesson(
            id="test-with-rels",
            trigger="test",
            action="Test action",
            relationships=[
                Relationship(target="prereq", type="prerequisite", weight=0.9),
                Relationship(target="alt", type="alternative", weight=0.5),
            ],
        )
        assert len(lesson.relationships) == 2
        assert lesson.relationships[0].type == "prerequisite"

    def test_graph_with_typed_relationships(self):
        """Test graph handles typed relationships."""
        graph = LessonGraph()

        lesson_a = Lesson(
            id="lesson-a",
            trigger="a",
            action="Lesson A",
            relationships=[
                Relationship(target="lesson-b", type="prerequisite", weight=0.8),
            ],
        )
        lesson_b = Lesson(
            id="lesson-b",
            trigger="b",
            action="Lesson B",
        )

        graph.add_lesson(lesson_a)
        graph.add_lesson(lesson_b)

        # Check the relationship was added
        related = graph.get_related("lesson-a")
        assert "lesson-b" in related

        # Check typed getter
        prereqs = graph.get_prerequisites("lesson-a")
        assert "lesson-b" in prereqs


class TestProjectContext:
    """Test project context functionality."""

    def test_create_project_context(self):
        """Test creating a project context."""
        ctx = ProjectContext(
            project_id="abc123",
            project_name="Test Project",
            project_path="/path/to/project",
            notes="Working on feature X",
        )
        assert ctx.project_id == "abc123"
        assert ctx.session_count == 0

    def test_project_todo(self):
        """Test project todo items."""
        todo = ProjectTodo(
            content="Implement feature Y",
            priority=5,
            notes="Blocked on API",
        )
        assert todo.status == "pending"
        assert todo.priority == 5

    def test_project_context_with_todos(self):
        """Test project context with todos."""
        ctx = ProjectContext(
            project_id="xyz789",
            project_name="My Project",
            project_path="/home/user/project",
            todos=[
                ProjectTodo(content="Task 1", status="completed"),
                ProjectTodo(content="Task 2", status="in_progress"),
                ProjectTodo(content="Task 3"),
            ],
        )
        pending = [t for t in ctx.todos if t.status in ("pending", "in_progress")]
        assert len(pending) == 2

    def test_project_context_to_string(self):
        """Test project context formatting."""
        ctx = ProjectContext(
            project_id="test",
            project_name="Test Project",
            project_path="/test",
            todos=[ProjectTodo(content="Do something")],
            recent_decisions=["Use TypeScript"],
        )
        output = ctx.to_context()
        assert "Test Project" in output
        assert "Do something" in output
        assert "Use TypeScript" in output

    @pytest.mark.asyncio
    async def test_save_and_get_project_context(self, temp_db):
        """Test persisting project context."""
        store = LessonStore(db_path=temp_db)

        ctx = ProjectContext(
            project_id="persist-test",
            project_name="Persistence Test",
            project_path="/tmp/test-project",
            notes="Testing persistence",
            active_files=["main.py", "utils.py"],
            todos=[ProjectTodo(content="Write tests")],
        )

        await store.save_project_context(ctx)

        # Retrieve
        retrieved = await store.get_project_context("persist-test")
        assert retrieved is not None
        assert retrieved.project_name == "Persistence Test"
        assert len(retrieved.active_files) == 2
        assert len(retrieved.todos) == 1

    @pytest.mark.asyncio
    async def test_get_project_context_by_path(self, temp_db):
        """Test retrieving project context by path."""
        store = LessonStore(db_path=temp_db)

        ctx = ProjectContext(
            project_id="path-test",
            project_name="Path Test",
            project_path="/unique/path/to/project",
        )

        await store.save_project_context(ctx)

        # Retrieve by path
        retrieved = await store.get_project_context_by_path("/unique/path/to/project")
        assert retrieved is not None
        assert retrieved.project_id == "path-test"
