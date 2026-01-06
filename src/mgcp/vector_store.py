"""Vector store for MGCP semantic lesson retrieval using ChromaDB."""

import os
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .models import Lesson, QueryResult

DEFAULT_CHROMA_PATH = "~/.mgcp/chroma"


class VectorStore:
    """Semantic search over lessons using ChromaDB."""

    def __init__(
        self,
        persist_path: str = DEFAULT_CHROMA_PATH,
        collection_name: str = "lessons",
    ):
        self.persist_path = Path(os.path.expanduser(persist_path))
        self.persist_path.mkdir(parents=True, exist_ok=True)

        # Initialize ChromaDB with persistence
        self.client = chromadb.PersistentClient(
            path=str(self.persist_path),
            settings=Settings(anonymized_telemetry=False),
        )

        # Get or create collection with embedding function
        # ChromaDB uses sentence-transformers by default
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_lesson(self, lesson: Lesson) -> None:
        """Add or update a lesson in the vector store."""
        # Combine trigger + action + rationale for embedding
        text = self._lesson_to_text(lesson)

        self.collection.upsert(
            ids=[lesson.id],
            documents=[text],
            metadatas=[{
                "trigger": lesson.trigger,
                "action": lesson.action,
                "tags": ",".join(lesson.tags),
                "parent_id": lesson.parent_id or "",
                "usage_count": lesson.usage_count,
            }],
        )

    def remove_lesson(self, lesson_id: str) -> None:
        """Remove a lesson from the vector store."""
        try:
            self.collection.delete(ids=[lesson_id])
        except Exception:
            pass  # Ignore if not found

    def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        tags: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """
        Search for relevant lessons.

        Returns:
            List of (lesson_id, score) tuples, sorted by relevance
        """
        # Build where clause for tag filtering
        where = None
        if tags:
            # ChromaDB where clause for tag matching
            where = {
                "$or": [{"tags": {"$contains": tag}} for tag in tags]
            }

        results = self.collection.query(
            query_texts=[query],
            n_results=limit,
            where=where,
            include=["distances", "metadatas"],
        )

        # Convert distances to similarity scores (ChromaDB returns distances)
        matches = []
        if results["ids"] and results["ids"][0]:
            for i, lesson_id in enumerate(results["ids"][0]):
                # Cosine distance to similarity: similarity = 1 - distance
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance

                if score >= min_score:
                    matches.append((lesson_id, score))

        return matches

    def search_similar(
        self,
        lesson_id: str,
        limit: int = 5,
    ) -> list[tuple[str, float]]:
        """Find lessons similar to a given lesson."""
        # Get the lesson's embedding
        result = self.collection.get(
            ids=[lesson_id],
            include=["embeddings", "documents"],
        )

        if not result["embeddings"] or not result["embeddings"][0]:
            return []

        # Query using the embedding
        embedding = result["embeddings"][0]
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=limit + 1,  # +1 to exclude self
            include=["distances"],
        )

        matches = []
        if results["ids"] and results["ids"][0]:
            for i, found_id in enumerate(results["ids"][0]):
                if found_id == lesson_id:
                    continue  # Skip self
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance
                matches.append((found_id, score))

        return matches[:limit]

    def get_all_ids(self) -> list[str]:
        """Get all lesson IDs in the store."""
        result = self.collection.get(include=[])
        return result["ids"] if result["ids"] else []

    def count(self) -> int:
        """Get total number of lessons in store."""
        return self.collection.count()

    def _lesson_to_text(self, lesson: Lesson) -> str:
        """Convert lesson to searchable text."""
        parts = [
            f"Trigger: {lesson.trigger}",
            f"Action: {lesson.action}",
        ]
        if lesson.rationale:
            parts.append(f"Rationale: {lesson.rationale}")
        if lesson.tags:
            parts.append(f"Tags: {', '.join(lesson.tags)}")
        if lesson.examples:
            for ex in lesson.examples[:2]:
                parts.append(f"Example ({ex.label}): {ex.code}")

        return "\n".join(parts)

    def rebuild_index(self, lessons: list[Lesson]) -> None:
        """Rebuild the entire index from a list of lessons."""
        # Clear existing
        existing_ids = self.get_all_ids()
        if existing_ids:
            self.collection.delete(ids=existing_ids)

        # Add all lessons
        for lesson in lessons:
            self.add_lesson(lesson)