"""Bootstrap runner - seeds the MGCP database with lessons and workflows.

This module orchestrates the bootstrap process by loading lessons, workflows,
and relationships from YAML files in bootstrap_data/:
- core/: MGCP tool usage lessons (task-agnostic)
- dev/: Software development lessons, security practices, and workflows

Usage:
    mgcp-bootstrap           # Seed all (core + dev)
    mgcp-bootstrap --core-only   # Seed only MGCP core lessons
    mgcp-bootstrap --dev-only    # Seed only development lessons/workflows
    mgcp-bootstrap --list        # Show available bootstrap modules
"""

import asyncio

from .bootstrap_loader import load_lessons, load_relationships, load_workflows
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


async def update_triggers(
    lessons: list, store: LessonStore, vector_store: QdrantVectorStore, graph: LessonGraph
) -> tuple[int, int, int]:
    """Update trigger fields on existing lessons without modifying other fields.

    Returns (updated, skipped, not_found) counts.
    """
    updated = 0
    skipped = 0
    not_found = 0

    for lesson in lessons:
        existing = await store.get_lesson(lesson.id)
        if not existing:
            print(f"  Not found: {lesson.id}")
            not_found += 1
            continue

        if existing.trigger == lesson.trigger:
            skipped += 1
            continue

        # Update only the trigger field, preserve everything else
        existing.trigger = lesson.trigger
        await store.update_lesson(existing)
        # Re-index in vector store (add_lesson uses upsert)
        vector_store.add_lesson(existing)
        graph.add_lesson(existing)
        print(f"  Updated {lesson.id}")
        updated += 1

    return updated, skipped, not_found


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
        core_lessons = load_lessons("core")
        core_rels = load_relationships("core")
        lessons_to_seed.extend(core_lessons)
        relationships_to_seed.extend(core_rels)
        print(f"Core: {len(core_lessons)} lessons, {len(core_rels)} relationships")

    if not core_only:
        dev_lessons = load_lessons("dev")
        dev_rels = load_relationships("dev")
        dev_workflows = load_workflows("dev")
        lessons_to_seed.extend(dev_lessons)
        relationships_to_seed.extend(dev_rels)
        workflows_to_seed.extend(dev_workflows)
        print(f"Dev: {len(dev_lessons)} lessons, {len(dev_rels)} relationships, {len(dev_workflows)} workflows")

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


async def run_update_triggers(core_only: bool = False, dev_only: bool = False) -> None:
    """Update trigger fields on existing bootstrap lessons."""
    store = LessonStore()
    vector_store = QdrantVectorStore()
    graph = LessonGraph()

    lessons_to_update = []
    if not dev_only:
        lessons_to_update.extend(load_lessons("core"))
    if not core_only:
        lessons_to_update.extend(load_lessons("dev"))

    print(f"Updating triggers for {len(lessons_to_update)} bootstrap lessons...\n")
    updated, skipped, not_found = await update_triggers(
        lessons_to_update, store, vector_store, graph
    )
    print(f"\nTriggers: {updated} updated, {skipped} unchanged, {not_found} not found")


def main():
    """Run bootstrap seeding."""
    import sys

    # Parse arguments
    core_only = "--core-only" in sys.argv
    dev_only = "--dev-only" in sys.argv
    do_update_triggers = "--update-triggers" in sys.argv

    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print("""MGCP Bootstrap - Seed database with lessons and workflows

Usage: mgcp-bootstrap [OPTIONS]

Options:
  -h, --help           Show this help message
  -V, --version        Show version number
  --core-only          Seed only MGCP core lessons (task-agnostic)
  --dev-only           Seed only development lessons and workflows
  --update-triggers    Update trigger fields on existing lessons (preserves all other fields)
  --list               Show available bootstrap modules

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
            core_lessons = load_lessons("core")
            core_rels = load_relationships("core")
            dev_lessons = load_lessons("dev")
            dev_rels = load_relationships("dev")
            dev_workflows = load_workflows("dev")
            print(f"""Available Bootstrap Modules:

CORE (bootstrap_data/core/):
  - MGCP tool usage patterns (query, save, catalogue, workflows, reminders)
  - Session lifecycle (start, end, shutdown)
  - Knowledge storage types and when to use each
  - Feedback loops and retrospectives
  Lessons: {len(core_lessons)} | Relationships: {len(core_rels)}

DEV (bootstrap_data/dev/):
  - Software development best practices
  - Security lessons (OWASP Secure Coding Practices)
  - Verification, testing, error handling, architecture, devops
  - Git workflow lessons
  - Accessibility, performance, data privacy
  - Workflows: feature-development, bug-fix, secure-code-review
  Lessons: {len(dev_lessons)} | Relationships: {len(dev_rels)} | Workflows: {len(dev_workflows)}
""")
            return

    if core_only and dev_only:
        print("Error: Cannot specify both --core-only and --dev-only")
        sys.exit(1)

    if do_update_triggers:
        print("MGCP Bootstrap - Updating triggers...\n")
        asyncio.run(run_update_triggers(core_only=core_only, dev_only=dev_only))
        return

    mode = "core only" if core_only else ("dev only" if dev_only else "all")
    print(f"MGCP Bootstrap - Seeding {mode}...\n")

    asyncio.run(seed_database(core_only=core_only, dev_only=dev_only))


if __name__ == "__main__":
    main()
