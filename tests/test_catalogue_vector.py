"""Tests for MGCP catalogue vector store."""

import tempfile

import pytest

from mgcp.models import (
    ArchitecturalNote,
    Convention,
    Decision,
    Dependency,
    ErrorPattern,
    FileCoupling,
    ProjectCatalogue,
    SecurityNote,
)
from mgcp.qdrant_catalogue_store import QdrantCatalogueStore

# Mark all tests in this module as slow - embedding operations are expensive in CI
pytestmark = pytest.mark.slow


@pytest.fixture
def temp_qdrant():
    """Create a temporary Qdrant store for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_catalogue():
    """Create a sample catalogue for testing."""
    return ProjectCatalogue(
        architecture_notes=[
            ArchitecturalNote(
                title="MCP Server Restart Required",
                description="After code changes, restart the MCP server",
                category="gotcha",
                related_files=["server.py"],
            ),
        ],
        security_notes=[
            SecurityNote(
                title="SQL Injection Risk",
                description="User input not sanitized in search",
                severity="high",
                status="open",
            ),
        ],
        frameworks=[
            Dependency(name="FastMCP", purpose="MCP server framework"),
        ],
        libraries=[
            Dependency(name="ChromaDB", purpose="Vector storage", version="0.4.x"),
        ],
        conventions=[
            Convention(
                title="Snake case for functions",
                rule="Use snake_case for all function names",
                category="naming",
                examples=["def get_user()", "def calculate_total()"],
            ),
        ],
        file_couplings=[
            FileCoupling(
                files=["server.py", "models.py"],
                reason="MCP tools depend on model definitions",
                direction="bidirectional",
            ),
        ],
        decisions=[
            Decision(
                title="Chose NetworkX over Neo4j",
                decision="Use NetworkX for graph storage",
                rationale="Simpler deployment, sufficient for current scale",
                alternatives=["Neo4j", "ArangoDB"],
            ),
        ],
        error_patterns=[
            ErrorPattern(
                error_signature="ModuleNotFoundError: mgcp",
                cause="Package not installed in editable mode",
                solution="Run pip install -e . from project root",
                related_files=["pyproject.toml"],
            ),
        ],
    )


class TestQdrantCatalogueStore:
    """Test catalogue vector store operations."""

    def test_index_catalogue(self, temp_qdrant, sample_catalogue):
        """Test indexing a full catalogue."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        count = store.index_catalogue("proj123", sample_catalogue)
        # 1 arch + 1 security + 1 framework + 1 library + 1 convention + 1 coupling + 1 decision + 1 error
        assert count == 8

    def test_search_arch_notes(self, temp_qdrant, sample_catalogue):
        """Test searching architectural notes."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        results = store.search("server restart", project_id="proj123")
        assert len(results) > 0
        assert any("arch" in r[2].get("item_type", "") for r in results)

    def test_search_by_project(self, temp_qdrant, sample_catalogue):
        """Test searching is scoped to project."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)
        store.index_catalogue("proj456", sample_catalogue)

        # Search specific project
        results = store.search("server restart", project_id="proj123")
        assert len(results) > 0
        assert all(r[2].get("project_id") == "proj123" for r in results)

    def test_search_by_item_type(self, temp_qdrant, sample_catalogue):
        """Test filtering by item type."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        results = store.search("injection", item_types=["security"])
        assert len(results) > 0
        assert all(r[2].get("item_type") == "security" for r in results)

    def test_search_conventions(self, temp_qdrant, sample_catalogue):
        """Test searching conventions."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        results = store.search("naming convention snake case")
        assert len(results) > 0
        assert any("convention" in r[2].get("item_type", "") for r in results)

    def test_search_decisions(self, temp_qdrant, sample_catalogue):
        """Test searching decisions."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        results = store.search("why NetworkX graph database")
        assert len(results) > 0
        assert any("decision" in r[2].get("item_type", "") for r in results)

    def test_search_error_patterns(self, temp_qdrant, sample_catalogue):
        """Test searching error patterns."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        results = store.search("ModuleNotFoundError import")
        assert len(results) > 0
        assert any("error" in r[2].get("item_type", "") for r in results)

    def test_search_file_couplings(self, temp_qdrant, sample_catalogue):
        """Test searching file couplings."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        results = store.search("server.py models.py change together")
        assert len(results) > 0

    def test_remove_item(self, temp_qdrant, sample_catalogue):
        """Test removing a single item."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        initial_count = store.count(project_id="proj123")

        store.remove_item("proj123", "arch", "MCP Server Restart Required")

        new_count = store.count(project_id="proj123")
        assert new_count == initial_count - 1

    def test_remove_project(self, temp_qdrant, sample_catalogue):
        """Test removing all items for a project."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)
        store.index_catalogue("proj456", sample_catalogue)

        store.remove_project("proj123")

        assert store.count(project_id="proj123") == 0
        assert store.count(project_id="proj456") > 0

    def test_count(self, temp_qdrant, sample_catalogue):
        """Test counting items."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        assert store.count() == 0

        store.index_catalogue("proj123", sample_catalogue)
        assert store.count() == 8
        assert store.count(project_id="proj123") == 8

    def test_min_score_filter(self, temp_qdrant, sample_catalogue):
        """Test minimum score filtering."""
        store = QdrantCatalogueStore(persist_path=temp_qdrant)
        store.index_catalogue("proj123", sample_catalogue)

        # High min_score should filter out low-relevance results
        results = store.search("completely unrelated query xyz", min_score=0.9)
        assert len(results) == 0


class TestNewCatalogueModels:
    """Test the new catalogue model types."""

    def test_convention_model(self):
        """Test Convention model."""
        conv = Convention(
            title="Test Convention",
            rule="Always do X",
            category="style",
            examples=["example1", "example2"],
        )
        assert conv.title == "Test Convention"
        assert conv.category == "style"
        assert len(conv.examples) == 2

    def test_file_coupling_model(self):
        """Test FileCoupling model."""
        coupling = FileCoupling(
            files=["a.py", "b.py"],
            reason="They share state",
            direction="bidirectional",
        )
        assert len(coupling.files) == 2
        assert coupling.direction == "bidirectional"

    def test_decision_model(self):
        """Test Decision model."""
        dec = Decision(
            title="Use React",
            decision="We will use React for the frontend",
            rationale="Team familiarity and ecosystem",
            alternatives=["Vue", "Angular"],
        )
        assert dec.title == "Use React"
        assert len(dec.alternatives) == 2

    def test_error_pattern_model(self):
        """Test ErrorPattern model."""
        err = ErrorPattern(
            error_signature="TypeError: cannot read property",
            cause="Null reference",
            solution="Check for null before accessing",
            related_files=["utils.js"],
        )
        assert "TypeError" in err.error_signature
        assert len(err.related_files) == 1

    def test_catalogue_with_new_fields(self):
        """Test ProjectCatalogue includes new fields."""
        cat = ProjectCatalogue(
            conventions=[Convention(title="Test", rule="Test rule")],
            file_couplings=[FileCoupling(files=["a.py"], reason="test")],
            decisions=[Decision(title="Test", decision="Test", rationale="Test")],
            error_patterns=[ErrorPattern(error_signature="Test", cause="Test", solution="Test")],
        )
        assert len(cat.conventions) == 1
        assert len(cat.file_couplings) == 1
        assert len(cat.decisions) == 1
        assert len(cat.error_patterns) == 1
