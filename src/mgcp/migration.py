"""Migration tool for ChromaDB to Qdrant.

This module handles migration of existing ChromaDB data to Qdrant with
the new BGE embedding model.

Migration Process:
1. Load lessons from SQLite (source of truth)
2. Re-embed with new BGE model (bge-base-en-v1.5, 768 dimensions)
3. Insert into Qdrant collections
4. Verify counts match

Rollback: SQLite contains all data; can always re-run migration.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .embedding import EMBEDDING_DIMENSION, MODEL_NAME
from .persistence import LessonStore
from .qdrant_catalogue_store import QdrantCatalogueStore
from .qdrant_vector_store import QdrantVectorStore

logger = logging.getLogger("mgcp.migration")


def get_default_data_dir() -> Path:
    """Get the default MGCP data directory."""
    data_dir = os.environ.get("MGCP_DATA_DIR")
    if data_dir:
        return Path(data_dir)
    return Path.home() / ".mgcp"


def check_chromadb_exists(data_dir: Path) -> bool:
    """Check if ChromaDB data exists."""
    chroma_path = data_dir / "chroma"
    return chroma_path.exists() and any(chroma_path.iterdir())


async def migrate(
    data_dir: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Migrate from ChromaDB to Qdrant.

    Args:
        data_dir: Data directory (default: ~/.mgcp)
        force: Force migration even if Qdrant data exists
        dry_run: Preview what would be migrated without making changes

    Returns:
        Dict with migration results
    """
    if data_dir is None:
        data_dir = get_default_data_dir()

    results = {
        "lessons_count": 0,
        "projects_count": 0,
        "catalogue_items_count": 0,
        "success": False,
        "errors": [],
    }

    # Check for existing ChromaDB data
    has_chromadb = check_chromadb_exists(data_dir)
    if has_chromadb:
        print(f"Found ChromaDB data at {data_dir / 'chroma'}")
    else:
        print("No existing ChromaDB data found. Starting fresh with Qdrant.")

    # Check for existing Qdrant data
    qdrant_path = data_dir / "qdrant"
    has_qdrant = qdrant_path.exists() and any(qdrant_path.iterdir())
    if has_qdrant and not force:
        print(f"Qdrant data already exists at {qdrant_path}")
        print("Use --force to overwrite existing Qdrant data")
        results["errors"].append("Qdrant data already exists")
        return results

    if dry_run:
        print("\n=== DRY RUN - No changes will be made ===\n")

    # Initialize SQLite store (source of truth)
    db_path = data_dir / "lessons.db"
    if not db_path.exists():
        print(f"No SQLite database found at {db_path}")
        print("Nothing to migrate.")
        results["success"] = True
        return results

    print(f"Loading data from {db_path}...")
    store = LessonStore(db_path=str(db_path))

    # Load lessons
    lessons = await store.get_all_lessons()
    results["lessons_count"] = len(lessons)
    print(f"  Found {len(lessons)} lessons")

    # Load project contexts
    contexts = await store.get_all_project_contexts()
    results["projects_count"] = len(contexts)
    print(f"  Found {len(contexts)} project contexts")

    # Count catalogue items
    total_catalogue = 0
    for ctx in contexts:
        cat = ctx.catalogue
        total_catalogue += (
            len(cat.architecture_notes) +
            len(cat.security_notes) +
            len(cat.frameworks) +
            len(cat.libraries) +
            len(cat.tools) +
            len(cat.conventions) +
            len(cat.file_couplings) +
            len(cat.decisions) +
            len(cat.error_patterns) +
            len(cat.custom_items)
        )
    results["catalogue_items_count"] = total_catalogue
    print(f"  Found {total_catalogue} catalogue items across all projects")

    # Load workflows
    workflows = await store.get_all_workflows()
    results["workflows_count"] = len(workflows)
    print(f"  Found {len(workflows)} workflows")

    if dry_run:
        print("\n=== Would migrate: ===")
        print(f"  {results['lessons_count']} lessons")
        print(f"  {results['projects_count']} project contexts")
        print(f"  {results['catalogue_items_count']} catalogue items")
        print(f"  {results['workflows_count']} workflows")
        print(f"\nUsing embedding model: {MODEL_NAME} ({EMBEDDING_DIMENSION} dimensions)")
        results["success"] = True
        return results

    # Clear Qdrant data if force
    if has_qdrant and force:
        print(f"\nClearing existing Qdrant data at {qdrant_path}...")
        import shutil
        shutil.rmtree(qdrant_path)

    # Initialize Qdrant stores
    print(f"\nInitializing Qdrant at {qdrant_path}...")
    print(f"Using embedding model: {MODEL_NAME} ({EMBEDDING_DIMENSION} dimensions)")
    print("(First run may take a moment to download the model)")

    qdrant_path_str = str(qdrant_path)
    vector_store = QdrantVectorStore(persist_path=qdrant_path_str)
    catalogue_store = QdrantCatalogueStore(persist_path=qdrant_path_str)

    # Migrate lessons
    if lessons:
        print(f"\nMigrating {len(lessons)} lessons...")
        try:
            vector_store.rebuild_index(lessons)
            print(f"  Migrated {vector_store.count()} lessons")
        except Exception as e:
            error = f"Failed to migrate lessons: {e}"
            print(f"  ERROR: {error}")
            results["errors"].append(error)
            return results

    # Migrate catalogue items
    if contexts:
        print(f"\nMigrating catalogue items from {len(contexts)} projects...")
        for ctx in contexts:
            try:
                count = catalogue_store.index_catalogue(ctx.project_id, ctx.catalogue)
                print(f"  {ctx.project_name or ctx.project_id}: {count} items")
            except Exception as e:
                error = f"Failed to migrate catalogue for {ctx.project_id}: {e}"
                print(f"  ERROR: {error}")
                results["errors"].append(error)

    # Migrate workflows
    if workflows:
        print(f"\nMigrating {len(workflows)} workflows...")
        for wf in workflows:
            try:
                searchable_text = f"{wf.name}. {wf.description}. Keywords: {wf.trigger}"
                vector_store.upsert_workflow(
                    workflow_id=wf.id,
                    searchable_text=searchable_text,
                    metadata={
                        "name": wf.name,
                        "description": wf.description,
                        "trigger": wf.trigger,
                        "step_count": len(wf.steps),
                    },
                )
            except Exception as e:
                error = f"Failed to migrate workflow {wf.id}: {e}"
                print(f"  ERROR: {error}")
                results["errors"].append(error)
        print(f"  Migrated {len(workflows)} workflows")

    # Verify counts
    print("\n=== Migration Summary ===")
    final_lessons = vector_store.count()
    final_catalogue = catalogue_store.count()
    print(f"  Lessons: {final_lessons} (expected {results['lessons_count']})")
    print(f"  Catalogue items: {final_catalogue} (expected {results['catalogue_items_count']})")

    if final_lessons != results["lessons_count"]:
        results["errors"].append(f"Lesson count mismatch: {final_lessons} vs {results['lessons_count']}")
    if final_catalogue != results["catalogue_items_count"]:
        results["errors"].append(f"Catalogue count mismatch: {final_catalogue} vs {results['catalogue_items_count']}")

    if results["errors"]:
        print("\nErrors encountered:")
        for error in results["errors"]:
            print(f"  - {error}")
        results["success"] = False
    else:
        print("\nMigration completed successfully!")
        results["success"] = True

    return results


def main():
    """CLI entry point for migration."""
    parser = argparse.ArgumentParser(
        description="Migrate MGCP data from ChromaDB to Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mgcp-migrate                    # Migrate with default settings
  mgcp-migrate --dry-run          # Preview what would be migrated
  mgcp-migrate --force            # Overwrite existing Qdrant data
  mgcp-migrate --data-dir /path   # Use custom data directory
        """,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Data directory (default: ~/.mgcp or MGCP_DATA_DIR)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force migration even if Qdrant data exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be migrated without making changes",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )

    # Run migration
    results = asyncio.run(migrate(
        data_dir=args.data_dir,
        force=args.force,
        dry_run=args.dry_run,
    ))

    # Exit code
    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()
