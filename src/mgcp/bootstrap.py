"""Bootstrap runner - seeds the MGCP database with lessons and workflows.

This module orchestrates the bootstrap process by importing and combining:
- bootstrap_core: MGCP tool usage lessons (task-agnostic)
- bootstrap_dev: Software development lessons and workflows (domain-specific)

Usage:
    mgcp-bootstrap           # Seed all (core + dev)
    mgcp-bootstrap --core-only   # Seed only MGCP core lessons
    mgcp-bootstrap --dev-only    # Seed only development lessons/workflows
    mgcp-bootstrap --list        # Show available bootstrap modules
"""

import asyncio

from .bootstrap_core import CORE_LESSONS, CORE_RELATIONSHIPS
from .bootstrap_dev import DEV_LESSONS, DEV_RELATIONSHIPS, DEV_WORKFLOWS
from .graph import LessonGraph
from .models import Relationship
from .persistence import LessonStore
from .qdrant_vector_store import QdrantVectorStore


async def seed_lessons(lessons: list, store: LessonStore, vector_store: QdrantVectorStore, graph: LessonGraph) -> tuple[int, int]:
    """Seed lessons into the database.

    Returns (added, skipped) counts.
    """
    added = 0
    skipped = 0

    for lesson in lessons:
        existing = await store.get_lesson(lesson.id)
        if existing:
            print(f"  Skipping {lesson.id} (already exists)")
            skipped += 1
            continue

        await store.add_lesson(lesson)
        vector_store.add_lesson(lesson)
        graph.add_lesson(lesson)
        print(f"  Added {lesson.id}")
        added += 1

    return added, skipped


async def seed_workflows(workflows: list, store: LessonStore) -> tuple[int, int]:
    """Seed workflows into the database.

    Returns (added, skipped) counts.
    """
    added = 0
    skipped = 0

    for workflow in workflows:
        existing = await store.get_workflow(workflow.id)
        if existing:
            print(f"  Skipping workflow {workflow.id} (already exists)")
            skipped += 1
            continue

        await store.save_workflow(workflow)
        print(f"  Added workflow {workflow.id} ({len(workflow.steps)} steps)")
        added += 1

    return added, skipped


async def seed_relationships(relationships: list, store: LessonStore, graph: LessonGraph) -> tuple[int, int]:
    """Seed lesson relationships into the database.

    Returns (added, skipped) counts.
    """
    added = 0
    skipped = 0

    for source_id, target_id, rel_type, context in relationships:
        source = await store.get_lesson(source_id)
        target = await store.get_lesson(target_id)

        if not source or not target:
            print(f"  Skipping {source_id} -> {target_id} (lesson not found)")
            skipped += 1
            continue

        # Check if relationship already exists
        existing = [r for r in source.relationships if r.target == target_id and r.type == rel_type]
        if existing:
            skipped += 1
            continue

        # Add relationship to source lesson
        new_rel = Relationship(
            target=target_id,
            type=rel_type,
            weight=0.7,
            context=[context],
            bidirectional=True,
        )
        source.relationships.append(new_rel)
        if target_id not in source.related_ids:
            source.related_ids.append(target_id)
        await store.update_lesson(source)

        # Add reverse relationship to target lesson
        reverse_type = rel_type
        if rel_type == "prerequisite":
            reverse_type = "sequence_next"
        elif rel_type == "sequence_next":
            reverse_type = "prerequisite"

        reverse_rel = Relationship(
            target=source_id,
            type=reverse_type,
            weight=0.7,
            context=[context],
            bidirectional=True,
        )
        if source_id not in [r.target for r in target.relationships]:
            target.relationships.append(reverse_rel)
            if source_id not in target.related_ids:
                target.related_ids.append(source_id)
            await store.update_lesson(target)

        # Update graph
        graph.add_lesson(source)
        graph.add_lesson(target)

        print(f"  Added {source_id} --[{rel_type}]--> {target_id}")
        added += 1

    return added, skipped


async def seed_database(core_only: bool = False, dev_only: bool = False) -> None:
    """Seed the database with bootstrap lessons and workflows.

    Args:
        core_only: If True, only seed MGCP core lessons
        dev_only: If True, only seed development lessons/workflows
    """
    store = LessonStore()
    vector_store = QdrantVectorStore()
    graph = LessonGraph()

    # Determine what to seed
    lessons_to_seed = []
    relationships_to_seed = []
    workflows_to_seed = []

    if not dev_only:
        lessons_to_seed.extend(CORE_LESSONS)
        relationships_to_seed.extend(CORE_RELATIONSHIPS)
        print(f"Core: {len(CORE_LESSONS)} lessons, {len(CORE_RELATIONSHIPS)} relationships")

    if not core_only:
        lessons_to_seed.extend(DEV_LESSONS)
        relationships_to_seed.extend(DEV_RELATIONSHIPS)
        workflows_to_seed.extend(DEV_WORKFLOWS)
        print(f"Dev: {len(DEV_LESSONS)} lessons, {len(DEV_RELATIONSHIPS)} relationships, {len(DEV_WORKFLOWS)} workflows")

    # Seed lessons
    if lessons_to_seed:
        print("\nSeeding lessons...")
        added, skipped = await seed_lessons(lessons_to_seed, store, vector_store, graph)
        print(f"\nLessons: {added} added, {skipped} skipped")
        print(f"Total lessons in database: {len(await store.get_all_lessons())}")

    # Seed workflows
    if workflows_to_seed:
        print("\nSeeding workflows...")
        added, skipped = await seed_workflows(workflows_to_seed, store)
        print(f"\nWorkflows: {added} added, {skipped} skipped")
        print(f"Total workflows in database: {len(await store.get_all_workflows())}")

    # Seed relationships
    if relationships_to_seed:
        print("\nSeeding relationships...")
        added, skipped = await seed_relationships(relationships_to_seed, store, graph)
        print(f"\nRelationships: {added} added, {skipped} skipped")


def main():
    """Run bootstrap seeding."""
    import sys

    # Parse arguments
    core_only = "--core-only" in sys.argv
    dev_only = "--dev-only" in sys.argv

    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print("""MGCP Bootstrap - Seed database with lessons and workflows

Usage: mgcp-bootstrap [OPTIONS]

Options:
  -h, --help     Show this help message
  -V, --version  Show version number
  --core-only    Seed only MGCP core lessons (task-agnostic)
  --dev-only     Seed only development lessons and workflows
  --list         Show available bootstrap modules

The bootstrap is safe to run multiple times - existing items will be skipped.
Data is stored in ~/.mgcp/ by default.

Bootstrap Modules:
  core  - MGCP tool usage patterns, session lifecycle, knowledge management
  dev   - Software development practices, security (OWASP), workflows
""")
            return
        elif sys.argv[1] in ("--version", "-V"):
            print("mgcp-bootstrap 1.1.0")
            return
        elif sys.argv[1] == "--list":
            print(f"""Available Bootstrap Modules:

CORE (bootstrap_core.py):
  - MGCP tool usage patterns (query, save, catalogue, workflows, reminders)
  - Session lifecycle (start, end, shutdown)
  - Knowledge storage types and when to use each
  - Feedback loops and retrospectives
  Lessons: {len(CORE_LESSONS)} | Relationships: {len(CORE_RELATIONSHIPS)}

DEV (bootstrap_dev.py):
  - Software development best practices
  - Security lessons (OWASP Secure Coding Practices)
  - Verification, testing, error handling
  - Git workflow lessons
  - Workflows: feature-development, bug-fix, secure-code-review
  Lessons: {len(DEV_LESSONS)} | Relationships: {len(DEV_RELATIONSHIPS)} | Workflows: {len(DEV_WORKFLOWS)}
""")
            return

    if core_only and dev_only:
        print("Error: Cannot specify both --core-only and --dev-only")
        sys.exit(1)

    mode = "core only" if core_only else ("dev only" if dev_only else "all")
    print(f"MGCP Bootstrap - Seeding {mode}...\n")

    asyncio.run(seed_database(core_only=core_only, dev_only=dev_only))


if __name__ == "__main__":
    main()
