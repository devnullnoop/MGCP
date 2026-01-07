"""Vector store for MGCP semantic project catalogue retrieval using ChromaDB."""

import os
from pathlib import Path
from typing import Literal

import chromadb
from chromadb.config import Settings

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

DEFAULT_CHROMA_PATH = "~/.mgcp/chroma"

ItemType = Literal[
    "arch", "security", "framework", "library", "tool",
    "convention", "coupling", "decision", "error"
]


class CatalogueVectorStore:
    """Semantic search over project catalogue items using ChromaDB."""

    def __init__(
        self,
        persist_path: str = DEFAULT_CHROMA_PATH,
        collection_name: str = "catalogue_items",
    ):
        self.persist_path = Path(os.path.expanduser(persist_path))
        self.persist_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.persist_path),
            settings=Settings(anonymized_telemetry=False),
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def _make_id(self, project_id: str, item_type: ItemType, identifier: str) -> str:
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

    def _add_arch_note(self, project_id: str, note: ArchitecturalNote) -> None:
        doc_id = self._make_id(project_id, "arch", note.title)
        text = self._arch_note_to_text(note)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": "arch",
                "title": note.title,
                "category": note.category,
                "related_files": ",".join(note.related_files),
            }],
        )

    def _add_security_note(self, project_id: str, note: SecurityNote) -> None:
        doc_id = self._make_id(project_id, "security", note.title)
        text = self._security_note_to_text(note)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": "security",
                "title": note.title,
                "severity": note.severity,
                "status": note.status,
            }],
        )

    def _add_dependency(self, project_id: str, dep: Dependency, dep_type: str) -> None:
        doc_id = self._make_id(project_id, dep_type, dep.name)
        text = self._dependency_to_text(dep, dep_type)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": dep_type,
                "name": dep.name,
                "version": dep.version or "",
                "purpose": dep.purpose,
            }],
        )

    def _add_convention(self, project_id: str, conv: Convention) -> None:
        doc_id = self._make_id(project_id, "convention", conv.title)
        text = self._convention_to_text(conv)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": "convention",
                "title": conv.title,
                "category": conv.category,
            }],
        )

    def _add_file_coupling(self, project_id: str, coupling: FileCoupling) -> None:
        # Use first file as identifier
        identifier = coupling.files[0] if coupling.files else "unknown"
        doc_id = self._make_id(project_id, "coupling", identifier)
        text = self._file_coupling_to_text(coupling)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": "coupling",
                "files": ",".join(coupling.files),
                "direction": coupling.direction,
            }],
        )

    def _add_decision(self, project_id: str, dec: Decision) -> None:
        doc_id = self._make_id(project_id, "decision", dec.title)
        text = self._decision_to_text(dec)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": "decision",
                "title": dec.title,
            }],
        )

    def _add_error_pattern(self, project_id: str, err: ErrorPattern) -> None:
        # Use first 30 chars of error signature as identifier
        identifier = err.error_signature[:30]
        doc_id = self._make_id(project_id, "error", identifier)
        text = self._error_pattern_to_text(err)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": "error",
                "error_signature": err.error_signature[:100],
                "related_files": ",".join(err.related_files),
            }],
        )

    def _add_custom_item(self, project_id: str, item: GenericCatalogueItem) -> None:
        """Add a custom/flexible catalogue item to the vector store."""
        doc_id = self._make_id(project_id, item.item_type, item.title)
        text = self._custom_item_to_text(item)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[{
                "project_id": project_id,
                "item_type": item.item_type,
                "title": item.title,
                "tags": ",".join(item.tags),
            }],
        )

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
        """Remove a single item from the vector store."""
        doc_id = self._make_id(project_id, item_type, identifier)
        try:
            self.collection.delete(ids=[doc_id])
        except Exception:
            pass

    def remove_project(self, project_id: str) -> None:
        """Remove all items for a project."""
        results = self.collection.get(
            where={"project_id": project_id},
            include=[],
        )
        if results["ids"]:
            self.collection.delete(ids=results["ids"])

    def search(
        self,
        query: str,
        project_id: str | None = None,
        item_types: list[ItemType] | None = None,
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[tuple[str, float, dict]]:
        """
        Search catalogue items.

        Args:
            query: Search query
            project_id: Filter to specific project (None for all projects)
            item_types: Filter to specific item types
            limit: Max results
            min_score: Minimum similarity threshold

        Returns:
            List of (doc_id, score, metadata) tuples
        """
        # Build where clause
        where_clauses = []
        if project_id:
            where_clauses.append({"project_id": project_id})
        if item_types:
            where_clauses.append({"item_type": {"$in": item_types}})

        where = None
        if len(where_clauses) == 1:
            where = where_clauses[0]
        elif len(where_clauses) > 1:
            where = {"$and": where_clauses}

        results = self.collection.query(
            query_texts=[query],
            n_results=limit,
            where=where,
            include=["distances", "metadatas"],
        )

        matches = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance
                if score >= min_score:
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                    matches.append((doc_id, score, metadata))

        return matches

    def count(self, project_id: str | None = None) -> int:
        """Get count of items, optionally filtered by project."""
        if project_id:
            results = self.collection.get(
                where={"project_id": project_id},
                include=[],
            )
            return len(results["ids"]) if results["ids"] else 0
        return self.collection.count()

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
