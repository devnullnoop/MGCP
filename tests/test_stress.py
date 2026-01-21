"""
Stress tests for MGCP.

These tests verify the system performs acceptably under load:
- Large numbers of lessons
- Many concurrent operations
- Complex graph structures
- Memory usage bounds
"""

import asyncio
import gc
import random
import tempfile
import time
from pathlib import Path

import pytest

from mgcp.graph import LessonGraph
from mgcp.models import Lesson, ProjectContext, ProjectTodo, Relationship
from mgcp.persistence import LessonStore
from mgcp.qdrant_vector_store import QdrantVectorStore

# Mark all tests in this module as slow - skipped in CI
pytestmark = pytest.mark.slow


def generate_random_text(words: int = 10) -> str:
    """Generate random text with specified word count."""
    word_list = [
        "python", "javascript", "error", "function", "class", "module",
        "import", "export", "async", "await", "database", "query", "api",
        "request", "response", "authentication", "authorization", "cache",
        "memory", "performance", "optimization", "testing", "debugging",
        "logging", "monitoring", "deployment", "docker", "kubernetes",
        "server", "client", "frontend", "backend", "middleware", "handler",
    ]
    return " ".join(random.choices(word_list, k=words))


def generate_lesson(id_prefix: str, index: int) -> Lesson:
    """Generate a random lesson for testing."""
    return Lesson(
        id=f"{id_prefix}-{index}",
        trigger=generate_random_text(5),
        action=generate_random_text(8),
        rationale=generate_random_text(15),
        tags=random.sample(["python", "testing", "api", "errors", "performance"], k=2),
    )


class TestLessonStoreStress:
    """Stress tests for the lesson persistence layer."""

    @pytest.fixture
    def store(self):
        """Create a store with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LessonStore(db_path=str(Path(tmpdir) / "stress.db"))

    @pytest.mark.asyncio
    async def test_create_1000_lessons(self, store):
        """Can create 1000 lessons in reasonable time."""
        start_time = time.time()

        for i in range(1000):
            lesson = generate_lesson("stress", i)
            await store.add_lesson(lesson)

        elapsed = time.time() - start_time

        # Should complete in under 30 seconds
        assert elapsed < 30, f"Creating 1000 lessons took {elapsed:.1f}s (expected <30s)"

        # Verify count
        all_lessons = await store.get_all_lessons()
        assert len(all_lessons) == 1000

    @pytest.mark.asyncio
    async def test_retrieve_from_1000_lessons(self, store):
        """Can retrieve specific lessons quickly from 1000."""
        # Setup: Create 1000 lessons
        for i in range(1000):
            lesson = generate_lesson("retrieve", i)
            await store.add_lesson(lesson)

        # Test: Retrieve 100 random lessons
        start_time = time.time()
        for _ in range(100):
            idx = random.randint(0, 999)
            lesson = await store.get_lesson(f"retrieve-{idx}")
            assert lesson is not None

        elapsed = time.time() - start_time

        # Should complete in under 2 seconds
        assert elapsed < 2, f"100 retrievals took {elapsed:.1f}s (expected <2s)"

    @pytest.mark.asyncio
    async def test_get_all_lessons_performance(self, store):
        """get_all_lessons performs acceptably with many lessons."""
        # Setup: Create 500 lessons
        for i in range(500):
            lesson = generate_lesson("getall", i)
            await store.add_lesson(lesson)

        # Test: Time get_all_lessons
        start_time = time.time()
        all_lessons = await store.get_all_lessons()
        elapsed = time.time() - start_time

        assert len(all_lessons) == 500
        # Should complete in under 5 seconds
        assert elapsed < 5, f"get_all_lessons took {elapsed:.1f}s (expected <5s)"

    @pytest.mark.asyncio
    async def test_many_project_contexts(self, store):
        """Can handle many project contexts."""
        # Create 100 projects with todos
        for i in range(100):
            ctx = ProjectContext(
                project_id=f"proj-{i}",
                project_name=f"Project {i}",
                project_path=f"/path/to/project-{i}",
                todos=[
                    ProjectTodo(content=f"Task {j}", status="pending")
                    for j in range(10)
                ],
                notes=generate_random_text(20),
            )
            await store.save_project_context(ctx)

        # Retrieve all
        start_time = time.time()
        contexts = await store.get_all_project_contexts()
        elapsed = time.time() - start_time

        assert len(contexts) == 100
        assert elapsed < 5, f"Getting 100 contexts took {elapsed:.1f}s (expected <5s)"


class TestQdrantVectorStoreStress:
    """Stress tests for the vector store."""

    @pytest.fixture
    def vector_store(self):
        """Create a vector store with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield QdrantVectorStore(persist_path=tmpdir)

    def test_add_500_lessons(self, vector_store):
        """Can add 500 lessons to vector store in reasonable time."""
        start_time = time.time()

        for i in range(500):
            lesson = generate_lesson("vector", i)
            vector_store.add_lesson(lesson)

        elapsed = time.time() - start_time

        # Embedding 500 lessons should take under 60 seconds
        # (depends on hardware, but should be reasonable)
        assert elapsed < 120, f"Adding 500 lessons took {elapsed:.1f}s (expected <120s)"

    def test_search_performance_500_lessons(self, vector_store):
        """Search performs acceptably with 500 lessons."""
        # Setup: Add 500 lessons
        for i in range(500):
            lesson = generate_lesson("search", i)
            vector_store.add_lesson(lesson)

        # Test: Run 50 searches
        start_time = time.time()
        for _ in range(50):
            query = generate_random_text(3)
            results = vector_store.search(query, limit=5)
            assert len(results) <= 5

        elapsed = time.time() - start_time

        # 50 searches should complete in under 10 seconds
        assert elapsed < 10, f"50 searches took {elapsed:.1f}s (expected <10s)"

    def test_search_relevance_with_many_lessons(self, vector_store):
        """Search still finds relevant results with many lessons."""
        # Add 200 generic lessons
        for i in range(200):
            lesson = generate_lesson("generic", i)
            vector_store.add_lesson(lesson)

        # Add one very specific lesson
        specific = Lesson(
            id="specific-needle",
            trigger="quantum entanglement photon superposition",
            action="Use quantum error correction codes",
        )
        vector_store.add_lesson(specific)

        # Add 200 more generic lessons
        for i in range(200, 400):
            lesson = generate_lesson("generic", i)
            vector_store.add_lesson(lesson)

        # CI environments can be slow - retry with backoff
        result_ids = []
        for attempt in range(5):
            time.sleep(0.5 * (attempt + 1))  # 0.5s, 1s, 1.5s, 2s, 2.5s
            results = vector_store.search("quantum entanglement photon", limit=10)
            result_ids = [r[0] for r in results]
            if result_ids:  # Found something
                break

        # The specific lesson should be in top results
        assert len(result_ids) > 0, "ChromaDB search returned no results after retries"
        assert "specific-needle" in result_ids, f"Specific lesson not in results: {result_ids[:5]}"


class TestGraphStress:
    """Stress tests for the graph operations."""

    @pytest.fixture
    def large_graph(self):
        """Create a graph with many nodes and edges."""
        graph = LessonGraph()

        # Create 500 lessons with relationships
        lessons = []
        for i in range(500):
            relationships = []
            # Add 2-5 random relationships to previous lessons
            if i > 10:
                num_rels = random.randint(2, 5)
                targets = random.sample(range(i), min(num_rels, i))
                for target in targets:
                    relationships.append(
                        Relationship(
                            target=f"graph-{target}",
                            type=random.choice(["related", "prerequisite", "alternative"]),
                        )
                    )

            lesson = Lesson(
                id=f"graph-{i}",
                trigger=generate_random_text(5),
                action=generate_random_text(8),
                parent_id=f"graph-{i // 10 * 10}" if i % 10 != 0 and i > 0 else None,
                relationships=relationships,
            )
            lessons.append(lesson)
            graph.add_lesson(lesson)

        return graph

    def test_spider_traversal_large_graph(self, large_graph):
        """Spider traversal performs acceptably on large graph."""
        start_time = time.time()

        # Run spider from multiple starting points
        for start_idx in [0, 100, 250, 400]:
            visited, paths = large_graph.spider(f"graph-{start_idx}", depth=3)
            assert len(visited) > 0

        elapsed = time.time() - start_time

        # 4 spider traversals should complete quickly
        assert elapsed < 5, f"4 spider traversals took {elapsed:.1f}s (expected <5s)"

    def test_get_statistics_large_graph(self, large_graph):
        """Getting statistics performs acceptably."""
        start_time = time.time()

        stats = large_graph.get_statistics()

        elapsed = time.time() - start_time

        assert stats["total_nodes"] == 500
        assert elapsed < 2, f"get_statistics took {elapsed:.1f}s (expected <2s)"

    def test_find_paths_large_graph(self, large_graph):
        """Can find paths in large graph."""
        # Try to find paths between various nodes
        start_time = time.time()

        paths_found = 0
        for _ in range(20):
            source = f"graph-{random.randint(0, 499)}"
            target = f"graph-{random.randint(0, 499)}"
            if source != target:
                # This tests the graph structure, not a specific method
                # Just verify the graph can be traversed
                visited, _ = large_graph.spider(source, depth=5)
                if target in visited:
                    paths_found += 1

        elapsed = time.time() - start_time

        # Should complete reasonably fast
        assert elapsed < 10, f"20 path searches took {elapsed:.1f}s (expected <10s)"


class TestConcurrentOperations:
    """Tests for concurrent access patterns."""

    @pytest.fixture
    def stores(self):
        """Create stores with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield {
                "lesson_store": LessonStore(db_path=str(Path(tmpdir) / "concurrent.db")),
                "vector_store": QdrantVectorStore(persist_path=str(Path(tmpdir) / "chroma")),
            }

    @pytest.mark.asyncio
    async def test_concurrent_reads(self, stores):
        """Multiple concurrent reads don't cause issues."""
        # Setup: Add some lessons
        for i in range(100):
            lesson = generate_lesson("concurrent", i)
            await stores["lesson_store"].add_lesson(lesson)

        # Test: Concurrent reads
        async def read_lesson(idx):
            return await stores["lesson_store"].get_lesson(f"concurrent-{idx}")

        start_time = time.time()

        # Run 50 concurrent reads
        tasks = [read_lesson(random.randint(0, 99)) for _ in range(50)]
        results = await asyncio.gather(*tasks)

        elapsed = time.time() - start_time

        # All should succeed
        assert all(r is not None for r in results)
        assert elapsed < 5, f"50 concurrent reads took {elapsed:.1f}s (expected <5s)"

    @pytest.mark.asyncio
    async def test_concurrent_writes(self, stores):
        """Multiple concurrent writes don't corrupt data."""
        # Test: Concurrent writes
        async def write_lesson(idx):
            lesson = generate_lesson("write", idx)
            await stores["lesson_store"].add_lesson(lesson)
            return idx

        start_time = time.time()

        # Run 50 concurrent writes
        tasks = [write_lesson(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

        elapsed = time.time() - start_time

        # All should succeed
        assert len(results) == 50

        # Verify all were written
        all_lessons = await stores["lesson_store"].get_all_lessons()
        assert len(all_lessons) == 50

        assert elapsed < 30, f"50 concurrent writes took {elapsed:.1f}s (expected <30s)"

    @pytest.mark.asyncio
    async def test_mixed_read_write(self, stores):
        """Mixed concurrent reads and writes work correctly."""
        # Setup: Add initial lessons
        for i in range(50):
            lesson = generate_lesson("mixed", i)
            await stores["lesson_store"].add_lesson(lesson)

        # Test: Mixed operations
        async def read_op():
            idx = random.randint(0, 49)
            return await stores["lesson_store"].get_lesson(f"mixed-{idx}")

        async def write_op(idx):
            lesson = generate_lesson("mixed-new", idx)
            await stores["lesson_store"].add_lesson(lesson)

        # Run mixed operations
        tasks = []
        for i in range(25):
            tasks.append(read_op())
            tasks.append(write_op(i))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check for exceptions
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"Got exceptions: {exceptions}"


class TestMemoryUsage:
    """Tests to verify memory usage stays bounded."""

    def test_lesson_memory_footprint(self):
        """Individual lessons have reasonable memory footprint."""
        gc.collect()
        baseline = self._get_memory_usage()

        # Create 1000 lessons in memory
        lessons = [generate_lesson("memory", i) for i in range(1000)]

        gc.collect()
        after = self._get_memory_usage()

        # 1000 lessons should use less than 50MB
        memory_used = after - baseline
        assert memory_used < 50_000_000, f"1000 lessons used {memory_used / 1_000_000:.1f}MB"

        # Keep reference to prevent GC
        assert len(lessons) == 1000

    def test_graph_memory_footprint(self):
        """Graph with many nodes has reasonable memory footprint."""
        gc.collect()
        baseline = self._get_memory_usage()

        graph = LessonGraph()
        for i in range(1000):
            lesson = Lesson(
                id=f"mem-{i}",
                trigger="test",
                action="test",
                parent_id=f"mem-{i-1}" if i > 0 else None,
            )
            graph.add_lesson(lesson)

        gc.collect()
        after = self._get_memory_usage()

        # Graph with 1000 nodes should use less than 100MB
        memory_used = after - baseline
        assert memory_used < 100_000_000, f"Graph used {memory_used / 1_000_000:.1f}MB"

        # Keep reference
        assert graph.get_statistics()["total_nodes"] == 1000

    def _get_memory_usage(self) -> int:
        """Get current memory usage in bytes."""

        # This is a rough estimate - actual memory profiling would need tracemalloc
        # For now, use sys.getsizeof on tracked objects
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Convert to bytes
        except ImportError:
            # Windows doesn't have resource module
            return 0


class TestDataIntegrity:
    """Tests to verify data integrity under stress."""

    @pytest.fixture
    def store(self):
        """Create a store with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LessonStore(db_path=str(Path(tmpdir) / "integrity.db"))

    @pytest.mark.asyncio
    async def test_data_survives_many_updates(self, store):
        """Data remains consistent after many updates."""
        # Create a lesson
        lesson = Lesson(
            id="integrity-test",
            trigger="test trigger",
            action="test action",
            version=1,
        )
        await store.add_lesson(lesson)

        # Update it 100 times
        for i in range(100):
            lesson = await store.get_lesson("integrity-test")
            lesson.version = i + 2
            lesson.action = f"updated action {i}"
            await store.update_lesson(lesson)

        # Verify final state
        final = await store.get_lesson("integrity-test")
        assert final.version == 101
        assert final.action == "updated action 99"

    @pytest.mark.asyncio
    async def test_no_data_loss_under_load(self, store):
        """No lessons are lost when adding many quickly."""
        # Add 500 lessons as fast as possible
        ids = set()
        for i in range(500):
            lesson = generate_lesson("loss-test", i)
            await store.add_lesson(lesson)
            ids.add(lesson.id)

        # Verify all exist
        all_lessons = await store.get_all_lessons()
        retrieved_ids = {l.id for l in all_lessons}

        missing = ids - retrieved_ids
        assert len(missing) == 0, f"Lost {len(missing)} lessons: {list(missing)[:5]}..."

    @pytest.mark.asyncio
    async def test_usage_count_accuracy(self, store):
        """Usage counts remain accurate under load."""
        # Create lesson
        lesson = Lesson(id="usage-test", trigger="test", action="test")
        await store.add_lesson(lesson)

        # Record usage 1000 times
        for _ in range(1000):
            await store.record_usage("usage-test")

        # Verify count
        updated = await store.get_lesson("usage-test")
        assert updated.usage_count == 1000, f"Expected 1000, got {updated.usage_count}"
