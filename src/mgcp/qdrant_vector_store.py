"""Vector store for MGCP semantic lesson retrieval using Qdrant.

Qdrant API Reference (v1.12+):
- Local mode: QdrantClient(path="~/.mgcp/qdrant")
- Server mode: QdrantClient(host="localhost", port=6333)
- Same API for both modes

Distance metric: Cosine (normalized embeddings)
Embedding model: BAAI/bge-base-en-v1.5 (768 dimensions)

Note: Qdrant local mode requires UUID point IDs. We use uuid5 with a
namespace to generate deterministic UUIDs from string lesson IDs, and
store the original ID in the payload for retrieval.
"""

import logging
import os
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .embedding import EMBEDDING_DIMENSION, embed, embed_batch
from .models import Lesson

logger = logging.getLogger("mgcp.qdrant_vector_store")

# Namespace for generating deterministic UUIDs from lesson IDs
MGCP_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def string_to_uuid(s: str) -> str:
    """Convert a string to a deterministic UUID string."""
    return str(uuid.uuid5(MGCP_NAMESPACE, s))


def get_default_qdrant_path() -> str:
    """Get the default Qdrant path, respecting MGCP_DATA_DIR env var."""
    data_dir = os.environ.get("MGCP_DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / "qdrant")
    return os.path.expanduser("~/.mgcp/qdrant")


DEFAULT_QDRANT_PATH = get_default_qdrant_path()


class QdrantVectorStore:
    """Semantic search over lessons using Qdrant.

    Provides the same interface as the ChromaDB-based VectorStore for
    drop-in replacement.
    """

    def __init__(
        self,
        persist_path: str = DEFAULT_QDRANT_PATH,
        collection_name: str = "lessons",
        client: QdrantClient | None = None,
    ):
        self.persist_path = Path(os.path.expanduser(persist_path))
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name

        # Use provided client or create new one
        # IMPORTANT: Qdrant local mode only allows ONE client per path.
        # Share clients between stores to avoid lock conflicts.
        if client is not None:
            self.client = client
            self._owns_client = False
        else:
            self.client = QdrantClient(path=str(self.persist_path))
            self._owns_client = True

        # Ensure collection exists
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create collection if it doesn't exist."""
        collections = self.client.get_collections().collections
        exists = any(c.name == self.collection_name for c in collections)

        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(f"Created collection '{self.collection_name}'")

    def add_lesson(self, lesson: Lesson) -> None:
        """Add or update a lesson in the vector store."""
        text = self._lesson_to_text(lesson)
        vector = embed(text)
        point_id = string_to_uuid(lesson.id)

        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "lesson_id": lesson.id,  # Store original ID for retrieval
                        "trigger": lesson.trigger,
                        "action": lesson.action,
                        "tags": ",".join(lesson.tags),
                        "parent_id": lesson.parent_id or "",
                        "usage_count": lesson.usage_count,
                        "text": text,  # Store for similarity search
                    },
                )
            ],
        )

    def remove_vector_lesson(self, lesson_id: str) -> bool:
        """Remove a lesson from the vector store.

        Returns:
            True if removal succeeded, False if not found or error occurred.
        """
        try:
            point_id = string_to_uuid(lesson_id)
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=[point_id],
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to remove lesson '{lesson_id}' from vector store: {e}")
            return False

    def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        tags: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Search for relevant lessons.

        Returns:
            List of (lesson_id, score) tuples, sorted by relevance
        """
        query_vector = embed(query)

        # Build filter for tag matching
        query_filter = None
        if tags:
            # Match if any of the tags are contained in the tags field
            # Qdrant doesn't have $contains, so we use keyword matching
            conditions = [
                FieldCondition(key="tags", match=MatchValue(value=tag))
                for tag in tags
            ]
            if len(conditions) == 1:
                query_filter = Filter(must=conditions)
            else:
                query_filter = Filter(should=conditions)

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=["lesson_id"],  # Need lesson_id to return original ID
        )

        matches = []
        for point in results.points:
            # Qdrant returns cosine similarity directly (0-1 for normalized vectors)
            score = point.score
            if score >= min_score:
                lesson_id = point.payload.get("lesson_id", str(point.id))
                matches.append((lesson_id, score))

        return matches

    def search_similar(
        self,
        lesson_id: str,
        limit: int = 5,
    ) -> list[tuple[str, float]]:
        """Find lessons similar to a given lesson."""
        # Get the lesson's vector
        point_id = string_to_uuid(lesson_id)
        points = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[point_id],
            with_vectors=True,
        )

        if not points:
            return []

        vector = points[0].vector

        # Query for similar, excluding self
        results = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=limit + 1,  # +1 to exclude self
            with_payload=["lesson_id"],
        )

        matches = []
        for point in results.points:
            point_lesson_id = point.payload.get("lesson_id", str(point.id))
            if point_lesson_id == lesson_id:
                continue  # Skip self
            matches.append((point_lesson_id, point.score))

        return matches[:limit]

    def get_all_ids(self) -> list[str]:
        """Get all lesson IDs in the store."""
        # Scroll through all points
        ids = []
        offset = None

        while True:
            result, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=1000,
                offset=offset,
                with_payload=["lesson_id"],
                with_vectors=False,
            )
            for p in result:
                lesson_id = p.payload.get("lesson_id", str(p.id))
                ids.append(lesson_id)
            if offset is None:
                break

        return ids

    def count(self) -> int:
        """Get total number of lessons in store."""
        info = self.client.get_collection(self.collection_name)
        return info.points_count

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
        # Clear existing by recreating collection
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass  # Collection might not exist

        self._ensure_collection()

        # Batch add all lessons
        if not lessons:
            return

        texts = [self._lesson_to_text(lesson) for lesson in lessons]
        vectors = embed_batch(texts)

        points = [
            PointStruct(
                id=string_to_uuid(lesson.id),
                vector=vector,
                payload={
                    "lesson_id": lesson.id,  # Store original ID for retrieval
                    "trigger": lesson.trigger,
                    "action": lesson.action,
                    "tags": ",".join(lesson.tags),
                    "parent_id": lesson.parent_id or "",
                    "usage_count": lesson.usage_count,
                    "text": texts[i],
                },
            )
            for i, (lesson, vector) in enumerate(zip(lessons, vectors))
        ]

        # Batch upsert
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

    # Workflow collection support (moved from server.py direct access)
    def get_or_create_workflow_collection(self) -> str:
        """Get or create the workflows collection.

        Returns the collection name for use in workflow queries.
        """
        workflow_collection = "workflows"
        collections = self.client.get_collections().collections
        exists = any(c.name == workflow_collection for c in collections)

        if not exists:
            self.client.create_collection(
                collection_name=workflow_collection,
                vectors_config=VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(f"Created collection '{workflow_collection}'")

        return workflow_collection

    def upsert_workflow(self, workflow_id: str, searchable_text: str, metadata: dict) -> None:
        """Upsert a workflow into the workflows collection."""
        collection = self.get_or_create_workflow_collection()
        vector = embed(searchable_text)
        point_id = string_to_uuid(workflow_id)

        self.client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "workflow_id": workflow_id,  # Store original ID
                        **metadata,
                        "text": searchable_text,
                    },
                )
            ],
        )

    def query_workflows(self, query: str, limit: int = 10) -> list[tuple[str, float, dict]]:
        """Query workflows by semantic similarity.

        Returns:
            List of (workflow_id, score, metadata) tuples
        """
        collection = self.get_or_create_workflow_collection()
        query_vector = embed(query)

        results = self.client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=limit,
            with_payload=True,
        )

        matches = []
        for point in results.points:
            payload = point.payload or {}
            workflow_id = payload.get("workflow_id", str(point.id))
            # Remove internal fields from metadata
            metadata = {k: v for k, v in payload.items() if k not in ("text", "workflow_id")}
            matches.append((workflow_id, point.score, metadata))

        return matches
