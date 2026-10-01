"""Basic tests for MGCP (Memory Graph Core Primitives)."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from mgcp.graph import LessonGraph
from mgcp.models import Example, Lesson, ProjectContext, ProjectTodo, Relationship
from mgcp.persistence import LessonStore
from mgcp.qdrant_vector_store import QdrantVectorStore


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

    def test_ancestor_walk_survives_a_parent_cycle(self):
        """A cycle from mgcp-import truncates the walk instead of hanging."""
        graph = LessonGraph()
        graph.add_lesson(Lesson(id="a", trigger="a", action="A", parent_id="b"))
        graph.add_lesson(Lesson(id="b", trigger="b", action="B", parent_id="a"))

        assert graph.get_ancestors("a") == ["b"]
        # get_statistics walks every node's ancestry, so the guard has to hold
        # for the whole graph, not just the node asked about.
        assert graph.get_statistics()["max_depth"] == 1

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


@pytest.mark.slow
class TestQdrantVectorStore:
    """Test Qdrant vector store operations."""

    def test_add_and_search(self, temp_qdrant, sample_lesson):
        """Test adding and searching lessons."""
        store = QdrantVectorStore(persist_path=temp_qdrant)
        store.add_lesson(sample_lesson)

        # Search
        results = store.search("test example", limit=5)
        assert len(results) > 0
        assert results[0][0] == "test-lesson"

    def test_remove_vector_lesson(self, temp_qdrant, sample_lesson):
        """Test removing a lesson from vector store."""
        store = QdrantVectorStore(persist_path=temp_qdrant)
        store.add_lesson(sample_lesson)

        # Remove
        store.remove_vector_lesson("test-lesson")

        # Verify removed
        assert "test-lesson" not in store.get_all_ids()

    def test_rebuild_index(self, temp_qdrant, sample_lesson):
        """Test rebuilding the index from scratch."""
        store = QdrantVectorStore(persist_path=temp_qdrant)
        store.add_lesson(sample_lesson)

        # Rebuild with new lessons
        new_lessons = [
            Lesson(id="new-1", trigger="new trigger 1", action="Action 1"),
            Lesson(id="new-2", trigger="new trigger 2", action="Action 2"),
        ]
        store.rebuild_index(new_lessons)

        # Old lesson should be gone
        assert store.count() == 2
        assert "test-lesson" not in store.get_all_ids()
        assert "new-1" in store.get_all_ids()
        assert "new-2" in store.get_all_ids()


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
        prereqs = graph.get_related("lesson-a", relation_type="prerequisite")
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


class TestSpiderTraversalCorrectness:
    """spider() regressions: nondeterminism, and dropping in-limit nodes."""

    @staticmethod
    def _diamond() -> LessonGraph:
        # root -> a -> deep, and root -> deep directly.
        # Reaching `deep` via `a` first (depth 2) used to mark it visited, so
        # the direct depth-1 arrival returned early and `leaf` (depth 2, well
        # inside a depth=2 spider) was never traversed.
        def rel(*targets):
            return [Relationship(target=t, type="related") for t in targets]

        g = LessonGraph()
        g.add_lesson(Lesson(id="root", trigger="t", action="a", relationships=rel("a", "deep")))
        g.add_lesson(Lesson(id="a", trigger="t", action="a", relationships=rel("deep")))
        g.add_lesson(Lesson(id="deep", trigger="t", action="a", relationships=rel("leaf")))
        g.add_lesson(Lesson(id="leaf", trigger="t", action="a"))
        return g

    def test_in_limit_node_is_not_dropped_by_a_deeper_first_visit(self):
        visited, _ = self._diamond().spider("root", depth=2)
        assert "leaf" in visited, (
            "leaf sits at depth 2 via root->deep->leaf and must be reached; "
            f"got {sorted(visited)}"
        )

    def test_repeated_traversals_agree(self):
        g = self._diamond()
        runs = {tuple(sorted(g.spider("root", depth=2)[0])) for _ in range(12)}
        assert len(runs) == 1, f"spider returned different node sets: {runs}"

    def test_get_related_order_is_stable(self):
        g = self._diamond()
        assert g.get_related("root") == sorted(g.get_related("root"))


class TestProcessExitsAfterStoreUse:
    """A process that opened a LessonStore must be able to exit.

    aiosqlite's connection worker thread is non-daemon, so a single connection
    still running at interpreter exit blocks threading._shutdown forever. The
    leak had one origin: when schema init raised on a corrupt database, the
    connection just created in `_acquire_conn` was neither pooled nor closed.
    `pytest tests/test_failure_recovery.py` — the file that deliberately
    corrupts databases — printed "28 passed in 3.1s" and then hung forever,
    which is why the suite could never be run to completion.

    Asserted in a subprocess: the thing under test is interpreter shutdown,
    which cannot be observed from inside the process performing it.
    """

    CORRUPT_DB = """
import asyncio, os, sys, tempfile
sys.path.insert(0, {src!r})
from mgcp.persistence import LessonStore

async def main():
    path = os.path.join(tempfile.mkdtemp(), "corrupt.db")
    with open(path, "wb") as fh:
        fh.write(b"not a sqlite database at all" * 64)
    store = LessonStore(path)
    try:
        await store.get_all_lessons()
    except Exception:
        pass          # the failure is expected; exiting afterwards is the test

asyncio.run(main())
print("done")
"""

    HEALTHY_DB = """
import asyncio, os, sys, tempfile
sys.path.insert(0, {src!r})
from mgcp.persistence import LessonStore
from mgcp.models import Lesson

async def main():
    store = LessonStore(os.path.join(tempfile.mkdtemp(), "ok.db"))
    await store.add_lesson(Lesson(id="x", trigger="t", action="a"))
    await store.get_lesson("x")
    # Deliberately no close_pool(): the exit path is what is under test.

asyncio.run(main())
print("done")
"""

    def _run_script(self, body: str) -> subprocess.CompletedProcess:
        src = str(Path(__file__).resolve().parent.parent / "src")
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(body.format(src=src))
            path = fh.name
        try:
            return subprocess.run(
                [sys.executable, path], capture_output=True, text=True, timeout=30
            )
        finally:
            os.unlink(path)

    @pytest.mark.parametrize(
        "script_name,what",
        [
            ("CORRUPT_DB", "a database that fails schema init"),
            ("HEALTHY_DB", "a normal store whose pool was never closed"),
        ],
    )
    def test_process_exits(self, script_name, what):
        try:
            result = self._run_script(getattr(self, script_name))
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"process did not exit within 30s after opening {what}: a "
                "non-daemon aiosqlite worker thread is still running at "
                "interpreter shutdown"
            )
        assert result.returncode == 0, result.stderr[-2000:]
        assert "done" in result.stdout


class TestQdrantModeResolution:
    """Embedded by default; server mode is opt-in via MGCP_QDRANT_URL.

    One resolver feeds every construction site so the two modes cannot
    disagree about which store a process is talking to — a session silently
    writing to the other store is the failure this centralisation prevents.
    """

    def test_embedded_is_the_default(self, monkeypatch):
        from mgcp.qdrant_vector_store import get_qdrant_url, qdrant_client_args

        monkeypatch.delenv("MGCP_QDRANT_URL", raising=False)
        assert get_qdrant_url() is None
        assert qdrant_client_args("/tmp/q") == {"path": "/tmp/q"}

    def test_url_switches_to_server_mode(self, monkeypatch):
        from mgcp.qdrant_vector_store import qdrant_client_args

        monkeypatch.setenv("MGCP_QDRANT_URL", "http://localhost:6333")
        monkeypatch.delenv("MGCP_QDRANT_API_KEY", raising=False)
        args = qdrant_client_args("/tmp/q")
        assert args == {"url": "http://localhost:6333"}
        assert "path" not in args, "server mode must not also pass a local path"

    def test_api_key_is_included_only_when_set(self, monkeypatch):
        from mgcp.qdrant_vector_store import qdrant_client_args

        monkeypatch.setenv("MGCP_QDRANT_URL", "http://localhost:6333")
        monkeypatch.setenv("MGCP_QDRANT_API_KEY", "k")
        assert qdrant_client_args("/tmp/q")["api_key"] == "k"
        monkeypatch.setenv("MGCP_QDRANT_API_KEY", "   ")
        assert "api_key" not in qdrant_client_args("/tmp/q")

    def test_blank_url_is_treated_as_unset(self, monkeypatch):
        """An empty env var must not produce url="" and a broken client."""
        from mgcp.qdrant_vector_store import qdrant_client_args

        monkeypatch.setenv("MGCP_QDRANT_URL", "   ")
        assert qdrant_client_args("/tmp/q") == {"path": "/tmp/q"}


@pytest.mark.integration
class TestQdrantServerModeConcurrency:
    """Two clients must be able to open the same collection at once.

    `_ensure_collection` is check-then-act, which is safe embedded (one client
    ever) and not safe in server mode: both processes see "not exists", both
    POST a create, and the loser got 409 Conflict and died on startup. Measured
    against a real server before it was fixed.

    Skipped unless MGCP_QDRANT_URL points at a reachable server, so the suite
    stays runnable without Docker.
    """

    @staticmethod
    def _server_available() -> bool:
        url = os.environ.get("MGCP_QDRANT_URL", "").strip()
        if not url:
            return False
        try:
            import urllib.request

            with urllib.request.urlopen(f"{url.rstrip('/')}/readyz", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def test_concurrent_open_of_a_new_collection(self):
        if not self._server_available():
            pytest.skip("no Qdrant server at MGCP_QDRANT_URL")

        import uuid as _uuid
        from concurrent.futures import ThreadPoolExecutor

        from mgcp.qdrant_vector_store import QdrantVectorStore

        name = f"race_{_uuid.uuid4().hex[:8]}"
        with ThreadPoolExecutor(max_workers=4) as pool:
            stores = list(pool.map(
                lambda _: QdrantVectorStore(collection_name=name), range(4)
            ))
        assert len(stores) == 4, "a concurrent opener failed to construct"
        assert all(s.server_mode for s in stores)
        stores[0].client.delete_collection(name)
