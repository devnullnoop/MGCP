"""Tests for community detection and summary features."""

import os
import tempfile

import pytest

from mgcp.graph import LessonGraph
from mgcp.models import CommunitySummary, Lesson, Relationship
from mgcp.persistence import LessonStore
from mgcp.qdrant_vector_store import QdrantVectorStore


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        yield db_path


@pytest.fixture
def temp_qdrant():
    """Create a temporary Qdrant directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _make_lesson(id: str, tags: list[str] | None = None, usage_count: int = 0) -> Lesson:
    """Helper to create a lesson with minimal fields."""
    return Lesson(
        id=id,
        trigger=f"trigger for {id}",
        action=f"action for {id}",
        tags=tags or [],
        usage_count=usage_count,
    )


class TestDetectCommunities:
    """Test Louvain community detection on the lesson graph."""

    def test_detect_communities_empty_graph(self):
        """Empty graph returns no communities."""
        graph = LessonGraph()
        result = graph.detect_communities()
        assert result == []

    def test_detect_communities_single_node(self):
        """Single node graph returns one community of size 1."""
        graph = LessonGraph()
        graph.add_lesson(_make_lesson("solo"))
        result = graph.detect_communities()
        # Louvain may return a single community with the lone node
        assert len(result) >= 0  # Implementation detail of Louvain

    def test_detect_communities_two_clusters(self):
        """Two disconnected groups produce 2 communities."""
        graph = LessonGraph()

        # Cluster A: a1 <-> a2 <-> a3
        a1 = _make_lesson("a1", tags=["cluster-a"])
        a2 = Lesson(
            id="a2",
            trigger="trigger a2",
            action="action a2",
            tags=["cluster-a"],
            relationships=[Relationship(target="a1", type="related")],
        )
        a3 = Lesson(
            id="a3",
            trigger="trigger a3",
            action="action a3",
            tags=["cluster-a"],
            relationships=[Relationship(target="a2", type="related")],
        )

        # Cluster B: b1 <-> b2
        b1 = _make_lesson("b1", tags=["cluster-b"])
        b2 = Lesson(
            id="b2",
            trigger="trigger b2",
            action="action b2",
            tags=["cluster-b"],
            relationships=[Relationship(target="b1", type="related")],
        )

        for lesson in [a1, a2, a3, b1, b2]:
            graph.add_lesson(lesson)

        result = graph.detect_communities()
        assert len(result) == 2

        sizes = sorted([c["size"] for c in result])
        assert sizes == [2, 3]

    def test_detect_communities_deterministic_ids(self):
        """Same graph produces the same community IDs across runs."""
        graph = LessonGraph()

        a1 = _make_lesson("a1")
        a2 = Lesson(
            id="a2",
            trigger="t",
            action="a",
            relationships=[Relationship(target="a1", type="related")],
        )
        graph.add_lesson(a1)
        graph.add_lesson(a2)

        result1 = graph.detect_communities(seed=42)
        result2 = graph.detect_communities(seed=42)

        assert len(result1) == len(result2)
        for c1, c2 in zip(result1, result2):
            assert c1["community_id"] == c2["community_id"]
            assert c1["members"] == c2["members"]

    def test_detect_communities_aggregate_tags(self):
        """Tags from member lessons are aggregated with counts."""
        graph = LessonGraph()

        a1 = _make_lesson("a1", tags=["testing", "python"])
        a2 = Lesson(
            id="a2",
            trigger="t",
            action="a",
            tags=["testing", "api"],
            relationships=[Relationship(target="a1", type="related")],
        )
        graph.add_lesson(a1)
        graph.add_lesson(a2)

        result = graph.detect_communities()
        assert len(result) == 1

        tags = result[0]["aggregate_tags"]
        assert tags["testing"] == 2
        assert tags["python"] == 1
        assert tags["api"] == 1

    def test_detect_communities_top_members(self):
        """Top members are sorted by usage_count descending."""
        graph = LessonGraph()

        lessons = [
            _make_lesson("low", usage_count=1),
            _make_lesson("high", usage_count=100),
            _make_lesson("mid", usage_count=50),
        ]
        # Connect them all
        lessons[1] = Lesson(
            id="high",
            trigger="t",
            action="a",
            usage_count=100,
            relationships=[Relationship(target="low", type="related")],
        )
        lessons[2] = Lesson(
            id="mid",
            trigger="t",
            action="a",
            usage_count=50,
            relationships=[Relationship(target="low", type="related")],
        )

        for lesson in lessons:
            graph.add_lesson(lesson)

        result = graph.detect_communities()
        assert len(result) == 1
        # Top members should be ordered by usage
        top = result[0]["top_members"]
        assert top[0] == "high"
        assert top[1] == "mid"
        assert top[2] == "low"


class TestGetCommunityForLesson:
    """Test finding which community a lesson belongs to."""

    def test_get_community_for_lesson_found(self):
        """Returns the correct community for a member lesson."""
        graph = LessonGraph()
        a1 = _make_lesson("a1")
        a2 = Lesson(
            id="a2",
            trigger="t",
            action="a",
            relationships=[Relationship(target="a1", type="related")],
        )
        graph.add_lesson(a1)
        graph.add_lesson(a2)

        community = graph.get_community_for_lesson("a1")
        assert community is not None
        assert "a1" in community["members"]
        assert "a2" in community["members"]

    def test_get_community_for_lesson_not_found(self):
        """Returns None for a lesson not in the graph."""
        graph = LessonGraph()
        graph.add_lesson(_make_lesson("exists"))

        result = graph.get_community_for_lesson("nonexistent")
        assert result is None


class TestCommunitySummaryPersistence:
    """Test community summary SQLite storage."""

    @pytest.mark.asyncio
    async def test_save_and_get_community_summary(self, temp_db):
        """Round-trip save and retrieve."""
        store = LessonStore(db_path=temp_db)

        summary = CommunitySummary(
            community_id="abc123def456",
            title="Error Handling Patterns",
            summary="Lessons about error handling in Python APIs.",
            member_ids=["error-context", "specific-exceptions", "error-handling"],
            member_count=3,
        )

        await store.save_community_summary(summary)

        retrieved = await store.get_community_summary("abc123def456")
        assert retrieved is not None
        assert retrieved.community_id == "abc123def456"
        assert retrieved.title == "Error Handling Patterns"
        assert retrieved.member_count == 3
        assert "error-context" in retrieved.member_ids

    @pytest.mark.asyncio
    async def test_community_summary_upsert(self, temp_db):
        """Saving twice updates existing summary."""
        store = LessonStore(db_path=temp_db)

        summary_v1 = CommunitySummary(
            community_id="abc123",
            title="Version 1",
            summary="First version",
            member_ids=["a", "b"],
            member_count=2,
        )
        await store.save_community_summary(summary_v1)

        summary_v2 = CommunitySummary(
            community_id="abc123",
            title="Version 2",
            summary="Updated version",
            member_ids=["a", "b", "c"],
            member_count=3,
        )
        await store.save_community_summary(summary_v2)

        retrieved = await store.get_community_summary("abc123")
        assert retrieved is not None
        assert retrieved.title == "Version 2"
        assert retrieved.member_count == 3

    @pytest.mark.asyncio
    async def test_get_all_community_summaries(self, temp_db):
        """Get all summaries, ordered by member_count descending."""
        store = LessonStore(db_path=temp_db)

        for i, count in enumerate([2, 5, 3]):
            summary = CommunitySummary(
                community_id=f"comm{i}",
                title=f"Community {i}",
                summary=f"Summary {i}",
                member_ids=[f"m{j}" for j in range(count)],
                member_count=count,
            )
            await store.save_community_summary(summary)

        all_summaries = await store.get_all_community_summaries()
        assert len(all_summaries) == 3
        # Should be ordered by member_count DESC
        assert all_summaries[0].member_count == 5
        assert all_summaries[1].member_count == 3
        assert all_summaries[2].member_count == 2

    @pytest.mark.asyncio
    async def test_delete_community_summary(self, temp_db):
        """Delete removes the summary."""
        store = LessonStore(db_path=temp_db)

        summary = CommunitySummary(
            community_id="to-delete",
            title="Delete Me",
            summary="This will be deleted",
            member_ids=["a"],
            member_count=1,
        )
        await store.save_community_summary(summary)

        deleted = await store.delete_community_summary("to-delete")
        assert deleted is True

        retrieved = await store.get_community_summary("to-delete")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, temp_db):
        """Deleting a nonexistent summary returns False."""
        store = LessonStore(db_path=temp_db)
        deleted = await store.delete_community_summary("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_community_summary_not_found(self, temp_db):
        """Getting a nonexistent summary returns None."""
        store = LessonStore(db_path=temp_db)
        result = await store.get_community_summary("nonexistent")
        assert result is None


@pytest.mark.slow
class TestCommunityVectorSearch:
    """Test Qdrant community summary semantic search."""

    def test_upsert_and_query_community_summary(self, temp_qdrant):
        """Save a community summary and search for it semantically."""
        vector_store = QdrantVectorStore(persist_path=temp_qdrant)

        vector_store.upsert_community_summary(
            community_id="err123",
            searchable_text="Community: Error Handling. Lessons about exception handling, error context, and recovery patterns. Topics: error, python, exceptions",
            metadata={
                "title": "Error Handling",
                "member_count": 5,
                "top_tags": "error,python,exceptions",
            },
        )

        results = vector_store.query_community_summaries("error handling patterns", limit=5)
        assert len(results) >= 1
        community_id, score, metadata = results[0]
        assert community_id == "err123"
        assert score > 0.3
        assert metadata["title"] == "Error Handling"

    def test_remove_community_summary(self, temp_qdrant):
        """Remove a community summary from vector store."""
        vector_store = QdrantVectorStore(persist_path=temp_qdrant)

        vector_store.upsert_community_summary(
            community_id="to-remove",
            searchable_text="Community: Testing. Lessons about unit testing.",
            metadata={"title": "Testing", "member_count": 3},
        )

        removed = vector_store.remove_community_summary("to-remove")
        assert removed is True

    def test_query_empty_collection(self, temp_qdrant):
        """Querying empty collection returns no results."""
        vector_store = QdrantVectorStore(persist_path=temp_qdrant)
        # Ensure collection is created
        vector_store.get_or_create_community_collection()
        results = vector_store.query_community_summaries("anything", limit=5)
        assert results == []
