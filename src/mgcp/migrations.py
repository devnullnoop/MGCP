"""Database migrations for MGCP (Memory Graph Control Protocol)."""

import asyncio
import json
import logging
from pathlib import Path

import aiosqlite

from .models import Relationship
from .persistence import DEFAULT_DB_PATH, LessonStore

logger = logging.getLogger(__name__)


async def migrate_related_ids_to_relationships(db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Migrate legacy related_ids to typed relationships.

    This converts the flat related_ids list to the new Relationship model
    with type='related', weight=0.5, bidirectional=True.

    Returns the number of lessons updated.
    """
    store = LessonStore(db_path)
    lessons = await store.get_all_lessons()

    updated_count = 0

    for lesson in lessons:
        # Skip if no legacy related_ids
        if not lesson.related_ids:
            continue

        # Check which related_ids are not already in relationships
        existing_targets = {r.target for r in lesson.relationships}
        new_relationships = []

        for related_id in lesson.related_ids:
            if related_id not in existing_targets:
                new_relationships.append(Relationship(
                    target=related_id,
                    type="related",
                    weight=0.5,
                    context=[],
                    bidirectional=True
                ))

        if new_relationships:
            lesson.relationships.extend(new_relationships)
            await store.update_lesson(lesson)
            updated_count += 1
            logger.info(f"Migrated {len(new_relationships)} relationships for {lesson.id}")

    return updated_count


async def ensure_bidirectional_relationships(db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Ensure all bidirectional relationships have their reverse counterpart.

    Returns the number of relationships added.
    """
    store = LessonStore(db_path)
    lessons = await store.get_all_lessons()

    # Build lookup map
    lesson_map = {l.id: l for l in lessons}

    added_count = 0

    for lesson in lessons:
        for rel in lesson.relationships:
            if not rel.bidirectional:
                continue

            # Check if reverse relationship exists
            target_lesson = lesson_map.get(rel.target)
            if not target_lesson:
                continue

            has_reverse = any(
                r.target == lesson.id
                for r in target_lesson.relationships
            )

            if not has_reverse:
                # Determine reverse relationship type
                reverse_type = rel.type
                if rel.type == "prerequisite":
                    reverse_type = "sequence_next"
                elif rel.type == "sequence_next":
                    reverse_type = "prerequisite"
                elif rel.type == "specializes":
                    reverse_type = "generalizes"
                elif rel.type == "generalizes":
                    reverse_type = "specializes"

                reverse_rel = Relationship(
                    target=lesson.id,
                    type=reverse_type,
                    weight=rel.weight,
                    context=rel.context,
                    bidirectional=True
                )

                target_lesson.relationships.append(reverse_rel)
                await store.update_lesson(target_lesson)
                added_count += 1
                logger.info(f"Added reverse relationship: {rel.target} -> {lesson.id}")

    return added_count


async def run_all_migrations(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Run all pending migrations."""
    results = {}

    logger.info("Starting migrations...")

    # Migration 1: Convert related_ids to relationships
    results["related_ids_migrated"] = await migrate_related_ids_to_relationships(db_path)

    # Migration 2: Ensure bidirectional relationships
    results["bidirectional_added"] = await ensure_bidirectional_relationships(db_path)

    logger.info(f"Migrations complete: {results}")
    return results


def main():
    """CLI entry point for running migrations."""
    import argparse

    parser = argparse.ArgumentParser(description="Run MGCP migrations")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    results = asyncio.run(run_all_migrations(args.db_path))
    print(f"Migration results: {results}")


if __name__ == "__main__":
    main()
