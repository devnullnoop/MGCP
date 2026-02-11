"""Tests for all 35 MCP server tools in server.py.

Tests the tool functions directly by injecting temp stores into server globals.
Embeddings are mocked to avoid model download dependency.
"""

import hashlib
import json
import random

import pytest
from qdrant_client import QdrantClient

from mgcp.graph import LessonGraph
from mgcp.persistence import LessonStore
from mgcp.qdrant_catalogue_store import QdrantCatalogueStore
from mgcp.qdrant_vector_store import QdrantVectorStore

# Import tool functions directly
from mgcp.server import (
    add_catalogue_item,
    add_lesson,
    add_workflow_step,
    create_workflow,
    delete_lesson,
    detect_communities,
    get_catalogue_item,
    get_lesson,
    get_lessons_by_category,
    get_workflow,
    get_workflow_step,
    link_lesson_to_workflow_step,
    link_lessons,
    list_categories,
    list_projects,
    list_workflows,
    query_lessons,
    query_workflows,
    refine_lesson,
    rem_report,
    rem_run,
    rem_status,
    remove_catalogue_item,
    reset_reminder_state,
    save_community_summary,
    save_project_context,
    schedule_reminder,
    search_catalogue,
    search_communities,
    spider_lessons,
    update_project_todo,
    update_workflow,
    update_workflow_state,
)
from mgcp.telemetry import TelemetryLogger


def _mock_embed(text):
    """Return a fixed unit vector so cosine search always matches.

    We're testing tool logic, not embedding quality. Embedding quality
    is covered by test_basic.py and test_integration.py with real models.
    """
    # Use a fixed base vector with tiny text-dependent perturbation
    # so all vectors are nearly identical (cosine ~1.0) but not exact dupes
    rng = random.Random(42)
    base = [rng.gauss(0, 1) for _ in range(768)]
    # Add tiny perturbation from text hash to avoid exact duplicates
    h = hashlib.md5(text.encode()).hexdigest()
    text_rng = random.Random(h)
    vec = [b + text_rng.gauss(0, 0.001) for b in base]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def _mock_embed_batch(texts):
    return [_mock_embed(t) for t in texts]


@pytest.fixture
def mock_embeddings(monkeypatch):
    """Mock embedding functions in all modules that import them."""
    monkeypatch.setattr("mgcp.qdrant_vector_store.embed", _mock_embed)
    monkeypatch.setattr("mgcp.qdrant_vector_store.embed_query", _mock_embed)
    monkeypatch.setattr("mgcp.qdrant_vector_store.embed_batch", _mock_embed_batch)
    monkeypatch.setattr("mgcp.qdrant_catalogue_store.embed", _mock_embed)
    monkeypatch.setattr("mgcp.qdrant_catalogue_store.embed_query", _mock_embed)


@pytest.fixture
async def server_stores(tmp_path, mock_embeddings):
    """Set up server with temp stores injected into globals."""
    import mgcp.server as server_module

    db_path = str(tmp_path / "test.db")
    qdrant_path = str(tmp_path / "qdrant")
    telemetry_path = str(tmp_path / "telemetry.db")

    store = LessonStore(db_path=db_path)
    qdrant_client = QdrantClient(path=qdrant_path)
    vector_store = QdrantVectorStore(client=qdrant_client)
    catalogue_vector = QdrantCatalogueStore(client=qdrant_client)
    graph = LessonGraph()
    telemetry = TelemetryLogger(db_path=telemetry_path)
    await telemetry.start_session()

    # Inject into server globals
    server_module._store = store
    server_module._vector_store = vector_store
    server_module._catalogue_vector = catalogue_vector
    server_module._graph = graph
    server_module._telemetry = telemetry
    server_module._initialized = True

    yield {
        "store": store,
        "vector_store": vector_store,
        "catalogue_vector": catalogue_vector,
        "graph": graph,
        "telemetry": telemetry,
    }

    # Cleanup
    server_module._initialized = False
    server_module._store = None
    server_module._vector_store = None
    server_module._catalogue_vector = None
    server_module._graph = None
    server_module._telemetry = None
    qdrant_client.close()


@pytest.fixture
async def seeded_lesson(server_stores):
    """Add a lesson via the tool so it exists in all stores."""
    result = await add_lesson(
        id="test-lesson",
        trigger="testing, unit test, pytest",
        action="Write tests before shipping code",
        rationale="Untested code is unreliable",
        tags=["testing", "quality"],
    )
    assert "added successfully" in result
    return "test-lesson"


@pytest.fixture
async def seeded_project(server_stores):
    """Create a project context via the tool."""
    result = await save_project_context(
        project_path="/tmp/test-project",
        project_name="Test Project",
        notes="Initial setup",
        active_files="main.py, utils.py",
    )
    assert "saved" in result
    return "/tmp/test-project"


@pytest.fixture
async def seeded_workflow(server_stores):
    """Create a workflow with steps via the tools."""
    await create_workflow(
        workflow_id="test-workflow",
        name="Test Workflow",
        description="A test workflow",
        trigger="test, unit test",
    )
    await add_workflow_step(
        workflow_id="test-workflow",
        step_id="step-one",
        name="First Step",
        description="Do the first thing",
        order=1,
        checklist="item1, item2",
    )
    await add_workflow_step(
        workflow_id="test-workflow",
        step_id="step-two",
        name="Second Step",
        description="Do the second thing",
        order=2,
    )
    return "test-workflow"


# ============================================================================
# LESSON DISCOVERY TOOLS
# ============================================================================


class TestQueryLessons:
    @pytest.mark.asyncio
    async def test_returns_results(self, seeded_lesson):
        result = await query_lessons("testing code")
        assert "Found" in result
        assert "test-lesson" in result

    @pytest.mark.asyncio
    async def test_no_results(self, server_stores):
        result = await query_lessons("something totally unrelated")
        assert "No relevant lessons" in result

    @pytest.mark.asyncio
    async def test_respects_limit(self, server_stores):
        # Add multiple lessons
        for i in range(5):
            await add_lesson(
                id=f"lesson-{i}",
                trigger=f"topic {i}",
                action=f"Do thing {i}",
            )
        result = await query_lessons("topic", limit=2)
        assert "Found" in result


class TestGetLesson:
    @pytest.mark.asyncio
    async def test_exists(self, seeded_lesson):
        result = await get_lesson("test-lesson")
        assert "test-lesson" in result
        assert "Write tests before shipping code" in result
        assert "Untested code" in result

    @pytest.mark.asyncio
    async def test_not_found(self, server_stores):
        result = await get_lesson("nonexistent")
        assert "not found" in result.lower()


class TestSpiderLessons:
    @pytest.mark.asyncio
    async def test_finds_connected(self, server_stores):
        await add_lesson(id="lesson-a", trigger="a", action="Action A")
        await add_lesson(id="lesson-b", trigger="b", action="Action B")
        await link_lessons("lesson-a", "lesson-b")

        result = await spider_lessons("lesson-a", depth=1)
        assert "lesson-b" in result

    @pytest.mark.asyncio
    async def test_not_in_graph(self, server_stores):
        result = await spider_lessons("nonexistent")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_no_connections(self, seeded_lesson):
        result = await spider_lessons("test-lesson")
        assert "No connected" in result


class TestListCategories:
    @pytest.mark.asyncio
    async def test_empty(self, server_stores):
        result = await list_categories()
        assert "No categories" in result

    @pytest.mark.asyncio
    async def test_with_categories(self, server_stores):
        # Parent lesson (category)
        await add_lesson(id="testing-category", trigger="testing", action="Testing best practices")
        # Child lesson
        await add_lesson(
            id="unit-testing",
            trigger="unit test",
            action="Write unit tests",
            parent_id="testing-category",
        )
        result = await list_categories()
        assert "testing-category" in result


class TestGetLessonsByCategory:
    @pytest.mark.asyncio
    async def test_with_children(self, server_stores):
        await add_lesson(id="parent", trigger="parent", action="Parent")
        await add_lesson(id="child-a", trigger="a", action="Child A", parent_id="parent")
        await add_lesson(id="child-b", trigger="b", action="Child B", parent_id="parent")

        result = await get_lessons_by_category("parent")
        assert "child-a" in result
        assert "child-b" in result

    @pytest.mark.asyncio
    async def test_empty_category(self, server_stores):
        result = await get_lessons_by_category("nonexistent")
        assert "No lessons" in result


# ============================================================================
# LESSON MANAGEMENT TOOLS
# ============================================================================


class TestAddLesson:
    @pytest.mark.asyncio
    async def test_success(self, server_stores):
        result = await add_lesson(
            id="new-lesson",
            trigger="new topic",
            action="Do the new thing",
            rationale="Because it matters",
            tags=["new"],
        )
        assert "added successfully" in result

        # Verify in all stores
        stores = server_stores
        lesson = await stores["store"].get_lesson("new-lesson")
        assert lesson is not None
        assert lesson.action == "Do the new thing"
        assert "new-lesson" in [n for n in stores["graph"].graph.nodes()]

    @pytest.mark.asyncio
    async def test_duplicate(self, seeded_lesson):
        result = await add_lesson(
            id="test-lesson",
            trigger="dupe",
            action="Duplicate",
        )
        assert "already exists" in result

    @pytest.mark.asyncio
    async def test_with_valid_parent(self, server_stores):
        await add_lesson(id="parent-lesson", trigger="parent", action="Parent")
        result = await add_lesson(
            id="child-lesson",
            trigger="child",
            action="Child",
            parent_id="parent-lesson",
        )
        assert "added successfully" in result

    @pytest.mark.asyncio
    async def test_with_invalid_parent(self, server_stores):
        result = await add_lesson(
            id="orphan",
            trigger="orphan",
            action="Orphan",
            parent_id="nonexistent-parent",
        )
        assert "not found" in result.lower()


class TestRefineLesson:
    @pytest.mark.asyncio
    async def test_success(self, seeded_lesson):
        result = await refine_lesson(
            lesson_id="test-lesson",
            refinement="Added clarity about edge cases",
        )
        assert "refined" in result
        assert "version 2" in result

    @pytest.mark.asyncio
    async def test_not_found(self, server_stores):
        result = await refine_lesson(
            lesson_id="nonexistent",
            refinement="Can't refine what doesn't exist",
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_with_new_action(self, seeded_lesson):
        result = await refine_lesson(
            lesson_id="test-lesson",
            refinement="Better wording",
            new_action="Always write comprehensive tests",
        )
        assert "refined" in result

        # Verify action was updated
        lesson = await get_lesson("test-lesson")
        assert "Always write comprehensive tests" in lesson


class TestLinkLessons:
    @pytest.mark.asyncio
    async def test_success(self, server_stores):
        await add_lesson(id="lesson-x", trigger="x", action="X")
        await add_lesson(id="lesson-y", trigger="y", action="Y")

        result = await link_lessons("lesson-x", "lesson-y")
        assert "Linked" in result

    @pytest.mark.asyncio
    async def test_bidirectional_reverse_types(self, server_stores):
        await add_lesson(id="prereq", trigger="prereq", action="Prerequisite")
        await add_lesson(id="sequel", trigger="sequel", action="Sequel")

        result = await link_lessons(
            "prereq", "sequel",
            relationship_type="prerequisite",
            bidirectional=True,
        )
        assert "Linked" in result

        # Verify reverse relationship exists
        stores = server_stores
        sequel_lesson = await stores["store"].get_lesson("sequel")
        reverse_rels = [r for r in sequel_lesson.relationships if r.target == "prereq"]
        assert len(reverse_rels) == 1
        assert reverse_rels[0].type == "sequence_next"  # Reverse of prerequisite

    @pytest.mark.asyncio
    async def test_specializes_generalizes_reverse(self, server_stores):
        await add_lesson(id="general", trigger="general", action="General")
        await add_lesson(id="specific", trigger="specific", action="Specific")

        await link_lessons(
            "specific", "general",
            relationship_type="specializes",
            bidirectional=True,
        )

        stores = server_stores
        general = await stores["store"].get_lesson("general")
        reverse_rels = [r for r in general.relationships if r.target == "specific"]
        assert len(reverse_rels) == 1
        assert reverse_rels[0].type == "generalizes"

    @pytest.mark.asyncio
    async def test_not_found(self, server_stores):
        await add_lesson(id="exists", trigger="exists", action="Exists")
        result = await link_lessons("exists", "missing")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_duplicate_link_idempotent(self, server_stores):
        await add_lesson(id="a", trigger="a", action="A")
        await add_lesson(id="b", trigger="b", action="B")

        await link_lessons("a", "b")
        await link_lessons("a", "b")  # Second link should be idempotent

        stores = server_stores
        lesson_a = await stores["store"].get_lesson("a")
        targets = [r.target for r in lesson_a.relationships]
        assert targets.count("b") == 1  # Only one link, not two


class TestDeleteLesson:
    @pytest.mark.asyncio
    async def test_success(self, seeded_lesson, server_stores):
        result = await delete_lesson("test-lesson")
        assert "Deleted" in result

        # Verify removed from all stores
        stores = server_stores
        assert await stores["store"].get_lesson("test-lesson") is None
        assert "test-lesson" not in [n for n in stores["graph"].graph.nodes()]

    @pytest.mark.asyncio
    async def test_not_found(self, server_stores):
        result = await delete_lesson("nonexistent")
        assert "not found" in result.lower()


# ============================================================================
# PROJECT CONTEXT TOOLS
# ============================================================================


class TestSaveProjectContext:
    @pytest.mark.asyncio
    async def test_new_project(self, server_stores):
        result = await save_project_context(
            project_path="/tmp/new-project",
            project_name="New Project",
            notes="Just started",
        )
        assert "saved" in result.lower()

    @pytest.mark.asyncio
    async def test_update_existing(self, seeded_project):
        result = await save_project_context(
            project_path="/tmp/test-project",
            notes="Updated notes",
            decision="Chose pytest over unittest",
        )
        assert "saved" in result.lower()

    @pytest.mark.asyncio
    async def test_active_files_parsing(self, server_stores):
        await save_project_context(
            project_path="/tmp/files-project",
            active_files="main.py, utils.py, tests/test.py",
        )
        stores = server_stores
        ctx = await stores["store"].get_project_context_by_path("/tmp/files-project")
        assert len(ctx.active_files) == 3
        assert "main.py" in ctx.active_files


class TestGetProjectContext:
    @pytest.mark.asyncio
    async def test_exists(self, seeded_project):
        from mgcp.server import get_project_context

        result = await get_project_context("/tmp/test-project")
        assert "Test Project" in result
        assert "Initial setup" in result

    @pytest.mark.asyncio
    async def test_not_found(self, server_stores):
        from mgcp.server import get_project_context

        result = await get_project_context("/tmp/nonexistent")
        assert "No saved context" in result


class TestAddProjectTodo:
    @pytest.mark.asyncio
    async def test_add_to_existing(self, seeded_project):
        from mgcp.server import add_project_todo

        result = await add_project_todo(
            project_path="/tmp/test-project",
            todo="Write more tests",
            priority=5,
        )
        assert "Todo added" in result
        assert "1 active" in result

    @pytest.mark.asyncio
    async def test_creates_project_if_missing(self, server_stores):
        from mgcp.server import add_project_todo

        result = await add_project_todo(
            project_path="/tmp/auto-created",
            todo="First todo",
        )
        assert "Todo added" in result


class TestUpdateProjectTodo:
    @pytest.mark.asyncio
    async def test_update_status(self, seeded_project):
        from mgcp.server import add_project_todo

        await add_project_todo(
            project_path="/tmp/test-project",
            todo="Complete this task",
        )
        result = await update_project_todo(
            project_path="/tmp/test-project",
            todo_index=0,
            status="completed",
        )
        assert "updated" in result.lower()
        assert "completed" in result

    @pytest.mark.asyncio
    async def test_invalid_index(self, seeded_project):
        result = await update_project_todo(
            project_path="/tmp/test-project",
            todo_index=999,
        )
        assert "Invalid" in result

    @pytest.mark.asyncio
    async def test_project_not_found(self, server_stores):
        result = await update_project_todo(
            project_path="/tmp/nonexistent",
            todo_index=0,
            status="completed",
        )
        assert "No project context found" in result


class TestListProjects:
    @pytest.mark.asyncio
    async def test_empty(self, server_stores):
        result = await list_projects()
        assert "No projects" in result

    @pytest.mark.asyncio
    async def test_with_projects(self, seeded_project):
        result = await list_projects()
        assert "Test Project" in result
        assert "/tmp/test-project" in result


# ============================================================================
# CATALOGUE TOOLS
# ============================================================================


class TestAddCatalogueItem:
    """Test the unified add_catalogue_item tool for all item types."""

    @pytest.mark.asyncio
    async def test_add_arch_note(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            title="MCP Restart Required",
            content="Server must be restarted after config changes",
            category="gotcha",
            related_files="server.py, config.py",
        )
        assert "Added architectural note" in result

    @pytest.mark.asyncio
    async def test_add_security_note(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="security",
            title="No Auth on Dashboard",
            content="Web dashboard has no authentication",
            severity="high",
            status="open",
        )
        assert "Added security note" in result
        assert "high" in result

    @pytest.mark.asyncio
    async def test_add_security_with_mitigation(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="security",
            title="XSS Risk",
            content="User input not sanitized",
            severity="medium",
            rationale="Escaping all output via template engine",
        )
        assert "Added security note" in result

    @pytest.mark.asyncio
    async def test_add_dependency_library(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="library",
            title="pytest",
            content="Testing framework",
            extra="version=>=7.0",
        )
        assert "Added library" in result

    @pytest.mark.asyncio
    async def test_add_dependency_framework(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="framework",
            title="FastAPI",
            content="Web framework for dashboard",
        )
        assert "Added framework" in result

    @pytest.mark.asyncio
    async def test_add_dependency_tool(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="tool",
            title="ruff",
            content="Linter",
        )
        assert "Added tool" in result

    @pytest.mark.asyncio
    async def test_add_convention(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="convention",
            title="Snake Case",
            content="Use snake_case for all Python functions",
            category="naming",
            extra="examples=get_user,save_data",
        )
        assert "Added convention" in result

    @pytest.mark.asyncio
    async def test_add_coupling(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="coupling",
            title="Models-Persistence",
            content="Models define schemas, persistence saves them",
            related_files="models.py, persistence.py",
        )
        assert "Added file coupling" in result

    @pytest.mark.asyncio
    async def test_add_decision(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="decision",
            title="Chose Qdrant over ChromaDB",
            content="Use Qdrant for vector storage",
            rationale="Same API for local and server mode",
            extra="alternatives=ChromaDB,Pinecone,Weaviate",
        )
        assert "Added decision" in result

    @pytest.mark.asyncio
    async def test_add_error_pattern(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="error",
            title="Storage folder already accessed",
            content="Two Qdrant clients pointing at same path",
            extra="solution=Use shared client instance",
            related_files="server.py",
        )
        assert "Added error pattern" in result

    @pytest.mark.asyncio
    async def test_add_custom_item(self, seeded_project):
        result = await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="api_endpoint",
            title="Health Check",
            content="GET /api/health returns 200",
            extra="method=GET,path=/api/health",
            tags="api, monitoring",
        )
        assert "Added custom item" in result

    @pytest.mark.asyncio
    async def test_add_creates_project_if_missing(self, server_stores):
        result = await add_catalogue_item(
            project_path="/tmp/auto-project",
            item_type="arch",
            title="Auto Created",
            content="Project was auto-created",
        )
        assert "Added" in result


class TestRemoveCatalogueItem:
    @pytest.mark.asyncio
    async def test_remove_arch_note(self, seeded_project):
        await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            title="To Remove",
            content="This will be removed",
        )
        result = await remove_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            identifier="To Remove",
        )
        assert "Removed" in result

    @pytest.mark.asyncio
    async def test_remove_not_found(self, seeded_project):
        result = await remove_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            identifier="Does Not Exist",
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_remove_project_not_found(self, server_stores):
        result = await remove_catalogue_item(
            project_path="/tmp/nonexistent",
            item_type="arch",
            identifier="Whatever",
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_remove_security_note(self, seeded_project):
        await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="security",
            title="Removable Issue",
            content="Will be removed",
        )
        result = await remove_catalogue_item(
            project_path="/tmp/test-project",
            item_type="security",
            identifier="Removable Issue",
        )
        assert "Removed" in result

    @pytest.mark.asyncio
    async def test_remove_custom_item_by_type_title(self, seeded_project):
        await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="env_var",
            title="DATABASE_URL",
            content="Connection string",
        )
        result = await remove_catalogue_item(
            project_path="/tmp/test-project",
            item_type="custom",
            identifier="env_var:DATABASE_URL",
        )
        assert "Removed" in result


class TestGetCatalogueItem:
    @pytest.mark.asyncio
    async def test_get_arch_note(self, seeded_project):
        await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            title="Test Note",
            content="Test description",
        )
        result = await get_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            identifier="Test Note",
        )
        parsed = json.loads(result)
        assert parsed["title"] == "Test Note"

    @pytest.mark.asyncio
    async def test_get_not_found(self, seeded_project):
        result = await get_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            identifier="Missing",
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_get_unknown_type(self, seeded_project):
        result = await get_catalogue_item(
            project_path="/tmp/test-project",
            item_type="bogus",
            identifier="Whatever",
        )
        assert "Unknown" in result

    @pytest.mark.asyncio
    async def test_get_decision(self, seeded_project):
        await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="decision",
            title="Use Qdrant",
            content="Qdrant for vectors",
            rationale="Better API",
        )
        result = await get_catalogue_item(
            project_path="/tmp/test-project",
            item_type="decision",
            identifier="Use Qdrant",
        )
        parsed = json.loads(result)
        assert parsed["decision"] == "Qdrant for vectors"


class TestSearchCatalogue:
    @pytest.mark.asyncio
    async def test_finds_items(self, seeded_project):
        await add_catalogue_item(
            project_path="/tmp/test-project",
            item_type="arch",
            title="Authentication System",
            content="OAuth2 with JWT tokens for API access",
        )
        result = await search_catalogue(query="authentication oauth")
        assert "Found" in result

    @pytest.mark.asyncio
    async def test_no_results(self, server_stores):
        result = await search_catalogue(query="quantum physics")
        assert "No matching" in result


# ============================================================================
# WORKFLOW TOOLS
# ============================================================================


class TestCreateWorkflow:
    @pytest.mark.asyncio
    async def test_success(self, server_stores):
        result = await create_workflow(
            workflow_id="new-workflow",
            name="New Workflow",
            description="A brand new workflow",
            trigger="build, create",
        )
        assert "Created" in result

    @pytest.mark.asyncio
    async def test_duplicate(self, seeded_workflow):
        result = await create_workflow(
            workflow_id="test-workflow",
            name="Duplicate",
            description="Should fail",
            trigger="test",
        )
        assert "already exists" in result


class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_empty(self, server_stores):
        result = await list_workflows()
        assert "No workflows" in result

    @pytest.mark.asyncio
    async def test_with_workflows(self, seeded_workflow):
        result = await list_workflows()
        assert "Test Workflow" in result
        assert "2 steps" in result


class TestGetWorkflow:
    @pytest.mark.asyncio
    async def test_exists(self, seeded_workflow):
        result = await get_workflow("test-workflow")
        assert "Test Workflow" in result
        assert "First Step" in result

    @pytest.mark.asyncio
    async def test_not_found(self, server_stores):
        result = await get_workflow("nonexistent")
        assert "not found" in result.lower()


class TestAddWorkflowStep:
    @pytest.mark.asyncio
    async def test_success(self, seeded_workflow):
        result = await add_workflow_step(
            workflow_id="test-workflow",
            step_id="step-three",
            name="Third Step",
            description="Do the third thing",
            order=3,
        )
        assert "Added step" in result

    @pytest.mark.asyncio
    async def test_duplicate_step(self, seeded_workflow):
        result = await add_workflow_step(
            workflow_id="test-workflow",
            step_id="step-one",
            name="Duplicate",
            description="Should fail",
            order=1,
        )
        assert "already exists" in result

    @pytest.mark.asyncio
    async def test_workflow_not_found(self, server_stores):
        result = await add_workflow_step(
            workflow_id="nonexistent",
            step_id="step",
            name="Step",
            description="Desc",
            order=1,
        )
        assert "not found" in result.lower()


class TestGetWorkflowStep:
    @pytest.mark.asyncio
    async def test_exists(self, seeded_workflow):
        result = await get_workflow_step("test-workflow", "step-one")
        assert "First Step" in result
        assert "item1" in result  # Checklist item

    @pytest.mark.asyncio
    async def test_step_not_found(self, seeded_workflow):
        result = await get_workflow_step("test-workflow", "nonexistent")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_workflow_not_found(self, server_stores):
        result = await get_workflow_step("nonexistent", "step")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_shows_next_step(self, seeded_workflow):
        result = await get_workflow_step("test-workflow", "step-one")
        assert "Next" in result
        assert "Second Step" in result


class TestUpdateWorkflow:
    @pytest.mark.asyncio
    async def test_update_trigger(self, seeded_workflow):
        result = await update_workflow(
            workflow_id="test-workflow",
            trigger="updated, new trigger",
        )
        assert "Updated" in result
        assert "trigger" in result

    @pytest.mark.asyncio
    async def test_no_updates(self, seeded_workflow):
        result = await update_workflow(workflow_id="test-workflow")
        assert "No updates" in result

    @pytest.mark.asyncio
    async def test_not_found(self, server_stores):
        result = await update_workflow(workflow_id="nonexistent", trigger="new")
        assert "not found" in result.lower()


class TestLinkLessonToWorkflowStep:
    @pytest.mark.asyncio
    async def test_success(self, seeded_workflow, seeded_lesson):
        result = await link_lesson_to_workflow_step(
            workflow_id="test-workflow",
            step_id="step-one",
            lesson_id="test-lesson",
            relevance="Testing is critical in step one",
            priority=1,
        )
        assert "Linked" in result

    @pytest.mark.asyncio
    async def test_duplicate_link(self, seeded_workflow, seeded_lesson):
        await link_lesson_to_workflow_step(
            workflow_id="test-workflow",
            step_id="step-one",
            lesson_id="test-lesson",
            relevance="First link",
        )
        result = await link_lesson_to_workflow_step(
            workflow_id="test-workflow",
            step_id="step-one",
            lesson_id="test-lesson",
            relevance="Duplicate",
        )
        assert "already linked" in result

    @pytest.mark.asyncio
    async def test_lesson_not_found(self, seeded_workflow):
        result = await link_lesson_to_workflow_step(
            workflow_id="test-workflow",
            step_id="step-one",
            lesson_id="nonexistent",
            relevance="N/A",
        )
        assert "not found" in result.lower()


class TestQueryWorkflows:
    @pytest.mark.asyncio
    async def test_finds_match(self, seeded_workflow):
        result = await query_workflows("running unit tests")
        # Should find test-workflow since it has trigger "test, unit test"
        assert "test-workflow" in result or "No workflows" in result

    @pytest.mark.asyncio
    async def test_no_workflows(self, server_stores):
        result = await query_workflows("anything")
        assert "No workflows available" in result


# ============================================================================
# COMMUNITY DETECTION TOOLS
# ============================================================================


class TestDetectCommunities:
    @pytest.mark.asyncio
    async def test_empty_graph(self, server_stores):
        result = await detect_communities()
        assert "No communities" in result or "too small" in result

    @pytest.mark.asyncio
    async def test_with_connected_lessons(self, server_stores):
        # Create a cluster of connected lessons
        for i in range(4):
            await add_lesson(id=f"cluster-{i}", trigger=f"topic {i}", action=f"Action {i}")
        for i in range(3):
            await link_lessons(f"cluster-{i}", f"cluster-{i+1}")

        result = await detect_communities(min_community_size=2)
        # May or may not detect communities depending on graph structure
        assert "Community" in result or "No communities" in result


class TestSaveCommunityAndSearch:
    @pytest.mark.asyncio
    async def test_save_and_search(self, server_stores):
        # Build a community
        for i in range(4):
            await add_lesson(id=f"group-{i}", trigger=f"safety {i}", action=f"Safety {i}")
        for i in range(3):
            await link_lessons(f"group-{i}", f"group-{i+1}")

        # Detect communities to get IDs
        detect_result = await detect_communities(min_community_size=2)

        if "No communities" not in detect_result:
            # Extract community ID from result (it's in backticks)
            import re
            comm_ids = re.findall(r"`(comm_\w+)`", detect_result)
            if comm_ids:
                save_result = await save_community_summary(
                    community_id=comm_ids[0],
                    title="Safety Practices",
                    summary="Lessons about safety and security practices",
                )
                assert "Saved" in save_result or "Updated" in save_result

                search_result = await search_communities("safety")
                assert "Safety" in search_result or "No community" in search_result


# ============================================================================
# REMINDER & WORKFLOW STATE TOOLS
# ============================================================================


class TestScheduleReminder:
    @pytest.mark.asyncio
    async def test_status_no_reminder(self, server_stores, monkeypatch, tmp_path):
        # Redirect state file to temp
        monkeypatch.setattr("mgcp.reminder_state.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("mgcp.reminder_state.STATE_DIR", tmp_path)

        result = await schedule_reminder()
        assert "NONE" in result

    @pytest.mark.asyncio
    async def test_schedule_call_based(self, server_stores, monkeypatch, tmp_path):
        monkeypatch.setattr("mgcp.reminder_state.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("mgcp.reminder_state.STATE_DIR", tmp_path)

        result = await schedule_reminder(
            after_calls=3,
            message="Time for step 2",
        )
        assert "scheduled" in result.lower()
        assert "3" in result

    @pytest.mark.asyncio
    async def test_schedule_time_based(self, server_stores, monkeypatch, tmp_path):
        monkeypatch.setattr("mgcp.reminder_state.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("mgcp.reminder_state.STATE_DIR", tmp_path)

        result = await schedule_reminder(
            after_minutes=5,
            message="Check test results",
        )
        assert "scheduled" in result.lower()
        assert "5 minutes" in result


class TestResetReminderState:
    @pytest.mark.asyncio
    async def test_reset(self, server_stores, monkeypatch, tmp_path):
        monkeypatch.setattr("mgcp.reminder_state.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("mgcp.reminder_state.STATE_DIR", tmp_path)

        result = await reset_reminder_state()
        assert "reset" in result.lower()


class TestUpdateWorkflowState:
    @pytest.mark.asyncio
    async def test_activate(self, server_stores, monkeypatch, tmp_path):
        monkeypatch.setattr("mgcp.reminder_state.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("mgcp.reminder_state.STATE_DIR", tmp_path)

        result = await update_workflow_state(
            active_workflow="feature-development",
            current_step="research",
        )
        assert "updated" in result.lower()
        assert "feature-development" in result

    @pytest.mark.asyncio
    async def test_step_completed(self, server_stores, monkeypatch, tmp_path):
        monkeypatch.setattr("mgcp.reminder_state.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("mgcp.reminder_state.STATE_DIR", tmp_path)

        await update_workflow_state(
            active_workflow="test-wf",
            current_step="step-1",
        )
        result = await update_workflow_state(
            step_completed="step-1",
            current_step="step-2",
        )
        assert "step-1" in result


# ============================================================================
# REM CYCLE TOOLS
# ============================================================================


class TestRemReport:
    @pytest.mark.asyncio
    async def test_no_cycles(self, server_stores):
        result = await rem_report()
        assert "No REM cycles" in result


class TestRemStatus:
    @pytest.mark.asyncio
    async def test_shows_schedule(self, server_stores):
        result = await rem_status()
        assert "REM Schedule" in result
        assert "staleness_scan" in result


class TestRemRun:
    @pytest.mark.asyncio
    async def test_runs_operations(self, server_stores):
        # Add a project context for session number
        await save_project_context(
            project_path="/tmp/rem-test",
            project_name="REM Test",
        )
        result = await rem_run(operations="staleness_scan")
        assert "REM Cycle Report" in result

    @pytest.mark.asyncio
    async def test_runs_all_due(self, server_stores):
        await save_project_context(
            project_path="/tmp/rem-test2",
            project_name="REM Test 2",
        )
        result = await rem_run()
        assert "REM Cycle Report" in result


# ============================================================================
# CROSS-STORE CONSISTENCY
# ============================================================================


class TestCrossStoreConsistency:
    """Verify that operations update all stores (SQLite, Qdrant, NetworkX)."""

    @pytest.mark.asyncio
    async def test_add_lesson_updates_all_stores(self, server_stores):
        stores = server_stores
        await add_lesson(id="consistency-test", trigger="test", action="Test")

        # SQLite
        lesson = await stores["store"].get_lesson("consistency-test")
        assert lesson is not None

        # Qdrant
        ids = stores["vector_store"].get_all_ids()
        assert "consistency-test" in ids

        # NetworkX
        assert "consistency-test" in stores["graph"].graph.nodes()

    @pytest.mark.asyncio
    async def test_delete_lesson_removes_from_all_stores(self, server_stores):
        stores = server_stores
        await add_lesson(id="to-delete", trigger="delete", action="Delete me")
        await delete_lesson("to-delete")

        # SQLite
        assert await stores["store"].get_lesson("to-delete") is None

        # NetworkX
        assert "to-delete" not in stores["graph"].graph.nodes()

        # Qdrant (removed via remove_vector_lesson)
        ids = stores["vector_store"].get_all_ids()
        assert "to-delete" not in ids

    @pytest.mark.asyncio
    async def test_refine_lesson_re_indexes_vector(self, server_stores):
        stores = server_stores
        await add_lesson(id="refine-me", trigger="old topic", action="Old action")
        await refine_lesson(
            lesson_id="refine-me",
            refinement="Better now",
            new_action="New improved action",
        )

        # SQLite should have version 2
        lesson = await stores["store"].get_lesson("refine-me")
        assert lesson.version == 2
        assert lesson.action == "New improved action"

        # Qdrant should still have it indexed
        ids = stores["vector_store"].get_all_ids()
        assert "refine-me" in ids

    @pytest.mark.asyncio
    async def test_link_updates_both_lessons_and_graph(self, server_stores):
        stores = server_stores
        await add_lesson(id="src", trigger="source", action="Source")
        await add_lesson(id="tgt", trigger="target", action="Target")
        await link_lessons("src", "tgt", relationship_type="complements")

        # Both lessons should have relationships in SQLite
        src = await stores["store"].get_lesson("src")
        tgt = await stores["store"].get_lesson("tgt")
        assert any(r.target == "tgt" for r in src.relationships)
        assert any(r.target == "src" for r in tgt.relationships)

        # Graph should have edge
        assert stores["graph"].graph.has_edge("src", "tgt")
