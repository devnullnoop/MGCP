"""Vector store for MGCP semantic project catalogue retrieval using Qdrant.

Qdrant API Reference (v1.12+):
- Local mode: QdrantClient(path="~/.mgcp/qdrant")
- Server mode: QdrantClient(host="localhost", port=6333)
- Same API for both modes

Distance metric: Cosine (normalized embeddings)
Embedding model: BAAI/bge-base-en-v1.5 (768 dimensions)

Note: Qdrant local mode requires UUID point IDs. We use uuid5 with a
namespace to generate deterministic UUIDs from string catalogue IDs, and
store the original ID in the payload for retrieval.
"""

import logging
import os
from pathlib import Path
from typing import Literal

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .embedding import EMBEDDING_DIMENSION, embed, embed_query
from .models import (
    ArchitecturalNote,
    Convention,
    Decision,
    Dependency,
    ErrorPattern,
    FileCoupling,
    GenericCatalogueItem,
    ProjectCatalogue,
    SecurityNote,
)

# Both stores share one Qdrant directory and one UUID namespace, so these were
# byte-identical copies here. Importing rather than re-declaring keeps a single
# definition: two copies of the namespace is two chances for point ids to diverge.
from .qdrant_vector_store import DEFAULT_QDRANT_PATH, string_to_uuid

logger = logging.getLogger("mgcp.qdrant_catalogue_store")

ItemType = Literal[
    "arch", "security", "framework", "library", "tool",
    "convention", "coupling", "decision", "error"
]


class QdrantCatalogueStore:
    """Semantic search over project catalogue items using Qdrant."""

    def __init__(
        self,
        persist_path: str = DEFAULT_QDRANT_PATH,
        collection_name: str = "catalogue_items",
        client: QdrantClient | None = None,
    ):
        self.persist_path = Path(os.path.expanduser(persist_path))
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name

        # IMPORTANT: Qdrant local mode only allows ONE client per path.
        # Share clients between stores to avoid lock conflicts.
        self.client = client or QdrantClient(path=str(self.persist_path))

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

    def _make_id(self, project_id: str, item_type: str, identifier: str) -> str:
        """Generate unique document ID."""
        safe_id = identifier.lower().replace(" ", "-")[:50]
        return f"{project_id}:{item_type}:{safe_id}"

    def index_catalogue(self, project_id: str, catalogue: ProjectCatalogue) -> int:
        """Index all items from a project catalogue. Returns count of items indexed."""
        count = 0

        # Index architectural notes
        for note in catalogue.architecture_notes:
            self._add_arch_note(project_id, note)
            count += 1

        # Index security notes
        for note in catalogue.security_notes:
            self._add_security_note(project_id, note)
            count += 1

        # Index dependencies
        for dep in catalogue.frameworks:
            self._add_dependency(project_id, dep, "framework")
            count += 1
        for dep in catalogue.libraries:
            self._add_dependency(project_id, dep, "library")
            count += 1
        for dep in catalogue.tools:
            self._add_dependency(project_id, dep, "tool")
            count += 1

        # Index conventions
        for conv in catalogue.conventions:
            self._add_convention(project_id, conv)
            count += 1

        # Index file couplings
        for coupling in catalogue.file_couplings:
            self._add_file_coupling(project_id, coupling)
            count += 1

        # Index decisions
        for dec in catalogue.decisions:
            self._add_decision(project_id, dec)
            count += 1

        # Index error patterns
        for err in catalogue.error_patterns:
            self._add_error_pattern(project_id, err)
            count += 1

        # Index custom items
        for item in catalogue.custom_items:
            self._add_custom_item(project_id, item)
            count += 1

        return count

    def _upsert_item(self, doc_id: str, text: str, metadata: dict) -> None:
        """Upsert a single item."""
        vector = embed(text)
        point_id = string_to_uuid(doc_id)
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "doc_id": doc_id,  # Store original ID for retrieval
                        **metadata,
                        "text": text,
                    },
                )
            ],
        )

    def _add_arch_note(self, project_id: str, note: ArchitecturalNote) -> None:
        doc_id = self._make_id(project_id, "arch", note.title)
        text = self._arch_note_to_text(note)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": "arch",
            "title": note.title,
            "category": note.category,
            "related_files": ",".join(note.related_files),
        })

    def _add_security_note(self, project_id: str, note: SecurityNote) -> None:
        doc_id = self._make_id(project_id, "security", note.title)
        text = self._security_note_to_text(note)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": "security",
            "title": note.title,
            "severity": note.severity,
            "status": note.status,
        })

    def _add_dependency(self, project_id: str, dep: Dependency, dep_type: str) -> None:
        doc_id = self._make_id(project_id, dep_type, dep.name)
        text = self._dependency_to_text(dep, dep_type)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": dep_type,
            "name": dep.name,
            "version": dep.version or "",
            "purpose": dep.purpose,
        })

    def _add_convention(self, project_id: str, conv: Convention) -> None:
        doc_id = self._make_id(project_id, "convention", conv.title)
        text = self._convention_to_text(conv)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": "convention",
            "title": conv.title,
            "category": conv.category,
        })

    def _add_file_coupling(self, project_id: str, coupling: FileCoupling) -> None:
        # Use first file as identifier
        identifier = coupling.files[0] if coupling.files else "unknown"
        doc_id = self._make_id(project_id, "coupling", identifier)
        text = self._file_coupling_to_text(coupling)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": "coupling",
            "files": ",".join(coupling.files),
            "direction": coupling.direction,
        })

    def _add_decision(self, project_id: str, dec: Decision) -> None:
        doc_id = self._make_id(project_id, "decision", dec.title)
        text = self._decision_to_text(dec)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": "decision",
            "title": dec.title,
        })

    def _add_error_pattern(self, project_id: str, err: ErrorPattern) -> None:
        # Use first 30 chars of error signature as identifier
        identifier = err.error_signature[:30]
        doc_id = self._make_id(project_id, "error", identifier)
        text = self._error_pattern_to_text(err)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": "error",
            "error_signature": err.error_signature[:100],
            "related_files": ",".join(err.related_files),
        })

    def _add_custom_item(self, project_id: str, item: GenericCatalogueItem) -> None:
        """Add a custom/flexible catalogue item to the vector store."""
        doc_id = self._make_id(project_id, item.item_type, item.title)
        text = self._custom_item_to_text(item)

        self._upsert_item(doc_id, text, {
            "project_id": project_id,
            "item_type": item.item_type,
            "title": item.title,
            "tags": ",".join(item.tags),
        })

    def _custom_item_to_text(self, item: GenericCatalogueItem) -> str:
        """Convert a custom catalogue item to searchable text."""
        parts = [
            f"Type: {item.item_type}",
            f"Title: {item.title}",
            f"Content: {item.content}",
        ]
        if item.tags:
            parts.append(f"Tags: {', '.join(item.tags)}")
        if item.metadata:
            parts.append(f"Metadata: {', '.join(f'{k}={v}' for k, v in item.metadata.items())}")
        return "\n".join(parts)

    def remove_item(self, project_id: str, item_type: ItemType, identifier: str) -> None:
        """Remove a single item from the vector store.

        Deleting an absent point is a no-op in Qdrant, so there is no
        not-found case to report. The warning below covers the one real
        failure: Qdrant erroring after the catalogue itself was already
        written, which leaves an orphaned vector.
        """
        doc_id = self._make_id(project_id, item_type, identifier)
        point_id = string_to_uuid(doc_id)
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=[point_id],
            )
        except Exception as e:
            logger.warning(f"Failed to remove catalogue item '{doc_id}': {e}")

    def remove_project(self, project_id: str) -> None:
        """Remove all items for a project."""
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=FilterSelector(filter=self._project_filter(project_id)),
        )

    def search(
        self,
        query: str,
        project_id: str | None = None,
        item_types: list[ItemType] | None = None,
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[tuple[str, float, dict]]:
        """Search catalogue items.

        Args:
            query: Search query
            project_id: Filter to specific project (None for all projects)
            item_types: Filter to specific item types
            limit: Max results
            min_score: Minimum similarity threshold

        Returns:
            List of (doc_id, score, metadata) tuples
        """
        query_vector = embed_query(query)

        # Build filter
        conditions = []
        if project_id:
            conditions.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))
        if item_types:
            conditions.append(FieldCondition(key="item_type", match=MatchAny(any=item_types)))

        query_filter = Filter(must=conditions) if conditions else None

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        matches = []
        for point in results.points:
            score = point.score
            if score >= min_score:
                payload = point.payload or {}
                doc_id = payload.get("doc_id", str(point.id))
                # Remove internal fields from metadata
                metadata = {k: v for k, v in payload.items() if k not in ("text", "doc_id")}
                matches.append((doc_id, score, metadata))

        return matches

    def count(self, project_id: str | None = None) -> int:
        """Get count of items, optionally filtered by project."""
        return self.client.count(
            collection_name=self.collection_name,
            count_filter=self._project_filter(project_id) if project_id else None,
        ).count

    @staticmethod
    def _project_filter(project_id: str) -> Filter:
        """Filter matching every item belonging to one project."""
        return Filter(
            must=[FieldCondition(key="project_id", match=MatchValue(value=project_id))]
        )

    # Text conversion methods
    def _arch_note_to_text(self, note: ArchitecturalNote) -> str:
        parts = [
            f"Title: {note.title}",
            f"Category: {note.category}",
            f"Description: {note.description}",
        ]
        if note.related_files:
            parts.append(f"Files: {', '.join(note.related_files)}")
        return "\n".join(parts)

    def _security_note_to_text(self, note: SecurityNote) -> str:
        parts = [
            f"Title: {note.title}",
            f"Severity: {note.severity}",
            f"Status: {note.status}",
            f"Description: {note.description}",
        ]
        if note.mitigation:
            parts.append(f"Mitigation: {note.mitigation}")
        return "\n".join(parts)

    def _dependency_to_text(self, dep: Dependency, dep_type: str) -> str:
        parts = [
            f"Name: {dep.name}",
            f"Type: {dep_type}",
            f"Purpose: {dep.purpose}",
        ]
        if dep.version:
            parts.append(f"Version: {dep.version}")
        if dep.notes:
            parts.append(f"Notes: {dep.notes}")
        return "\n".join(parts)

    def _convention_to_text(self, conv: Convention) -> str:
        parts = [
            f"Title: {conv.title}",
            f"Category: {conv.category}",
            f"Rule: {conv.rule}",
        ]
        if conv.examples:
            parts.append(f"Examples: {', '.join(conv.examples[:3])}")
        return "\n".join(parts)

    def _file_coupling_to_text(self, coupling: FileCoupling) -> str:
        return "\n".join([
            f"Files: {', '.join(coupling.files)}",
            f"Reason: {coupling.reason}",
            f"Direction: {coupling.direction}",
        ])

    def _decision_to_text(self, dec: Decision) -> str:
        parts = [
            f"Title: {dec.title}",
            f"Decision: {dec.decision}",
            f"Rationale: {dec.rationale}",
        ]
        if dec.alternatives:
            parts.append(f"Alternatives considered: {', '.join(dec.alternatives)}")
        return "\n".join(parts)

    def _error_pattern_to_text(self, err: ErrorPattern) -> str:
        parts = [
            f"Error: {err.error_signature}",
            f"Cause: {err.cause}",
            f"Solution: {err.solution}",
        ]
        if err.related_files:
            parts.append(f"Files: {', '.join(err.related_files)}")
        return "\n".join(parts)
