"""Database migrations for MGCP (Memory Graph Core Primitives)."""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .models import Relationship
from .persistence import DEFAULT_DB_PATH, SCHEMA, LessonStore

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


async def deduplicate_project_contexts(db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Deduplicate project contexts that have the same project_path but different project_ids.

    When duplicates exist, merges data into a single record using the SHA256-based
    project_id and keeps the most complete data (highest session count, most recent access).

    Returns the number of duplicates removed.
    """
    import hashlib
    import os

    db_full_path = Path(os.path.expanduser(db_path))
    if not db_full_path.exists():
        return 0

    async with aiosqlite.connect(db_full_path) as conn:
        conn.row_factory = aiosqlite.Row

        # Find all project_paths that have duplicates
        cursor = await conn.execute("""
            SELECT project_path, COUNT(*) as cnt
            FROM project_contexts
            GROUP BY project_path
            HAVING COUNT(*) > 1
        """)
        duplicates = await cursor.fetchall()

        removed_count = 0

        for dup in duplicates:
            project_path = dup["project_path"]
            logger.info(f"Found duplicate projects for path: {project_path}")

            # Get all records for this path
            cursor = await conn.execute(
                "SELECT * FROM project_contexts WHERE project_path = ? ORDER BY session_count DESC, last_accessed DESC",
                (project_path,)
            )
            records = await cursor.fetchall()

            # Generate the correct SHA256-based project_id
            correct_id = hashlib.sha256(project_path.encode()).hexdigest()[:12]

            # Find the best record to keep (highest session count, most recent)
            best_record = records[0]

            # Merge data from all records
            merged_session_count = sum(r["session_count"] for r in records)
            merged_decisions = []
            for r in records:
                decisions = json.loads(r["recent_decisions"]) if r["recent_decisions"] else []
                merged_decisions.extend(decisions)
            merged_decisions = merged_decisions[-10:]  # Keep last 10

            # Delete all records for this path
            await conn.execute(
                "DELETE FROM project_contexts WHERE project_path = ?",
                (project_path,)
            )

            # Insert the merged record with correct project_id
            await conn.execute(
                """
                INSERT INTO project_contexts (
                    project_id, project_name, project_path, catalogue, todos, active_files,
                    recent_decisions, last_session_id, last_accessed, session_count, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    correct_id,
                    best_record["project_name"],
                    project_path,
                    best_record["catalogue"],
                    best_record["todos"],
                    best_record["active_files"],
                    json.dumps(merged_decisions),
                    best_record["last_session_id"],
                    best_record["last_accessed"],
                    merged_session_count,
                    best_record["notes"],
                )
            )

            removed_count += len(records) - 1
            logger.info(f"Merged {len(records)} records into one with id={correct_id}")

        await conn.commit()

    return removed_count


async def ensure_unique_project_path(db_path: str = DEFAULT_DB_PATH) -> bool:
    """
    Ensure project_path has a UNIQUE constraint.

    SQLite doesn't support adding constraints to existing tables, so we recreate
    the table if needed. Only runs if duplicates have been removed first.

    Returns True if migration was performed.
    """
    import os

    db_full_path = Path(os.path.expanduser(db_path))
    if not db_full_path.exists():
        return False

    async with aiosqlite.connect(db_full_path) as conn:
        # Check if UNIQUE constraint already exists
        cursor = await conn.execute("PRAGMA index_list(project_contexts)")
        indexes = await cursor.fetchall()

        has_unique = False
        for idx in indexes:
            if idx[2] == 1:  # unique flag
                cursor = await conn.execute(f"PRAGMA index_info({idx[1]})")
                columns = await cursor.fetchall()
                for col in columns:
                    if col[2] == "project_path":
                        has_unique = True
                        break

        if has_unique:
            logger.info("project_path already has UNIQUE constraint")
            return False

        # Check for duplicates first - don't add constraint if duplicates exist
        cursor = await conn.execute("""
            SELECT project_path, COUNT(*) as cnt
            FROM project_contexts
            GROUP BY project_path
            HAVING COUNT(*) > 1
        """)
        if await cursor.fetchone():
            logger.warning(
                "Cannot add UNIQUE constraint - duplicates still exist. "
                "Run deduplicate_project_contexts first."
            )
            return False

        # Recreate table with UNIQUE constraint
        logger.info("Recreating project_contexts table with UNIQUE constraint on project_path")

        await conn.executescript("""
            -- Create new table with UNIQUE constraint
            CREATE TABLE project_contexts_new (
                project_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL,
                project_path TEXT NOT NULL UNIQUE,
                catalogue JSON NOT NULL DEFAULT '{}',
                todos JSON NOT NULL DEFAULT '[]',
                active_files JSON NOT NULL DEFAULT '[]',
                recent_decisions JSON NOT NULL DEFAULT '[]',
                last_session_id TEXT,
                last_accessed TEXT NOT NULL,
                session_count INTEGER DEFAULT 0,
                notes TEXT
            );

            -- Copy data
            INSERT INTO project_contexts_new SELECT * FROM project_contexts;

            -- Drop old table
            DROP TABLE project_contexts;

            -- Rename new table
            ALTER TABLE project_contexts_new RENAME TO project_contexts;

            -- Recreate index
            CREATE INDEX IF NOT EXISTS idx_project_path ON project_contexts(project_path);
        """)

        await conn.commit()
        logger.info("Successfully added UNIQUE constraint to project_path")
        return True


async def ensure_version_tables(db_path: str = DEFAULT_DB_PATH) -> bool:
    """
    Ensure lesson_versions, context_history, and rem_state tables exist.

    These are new tables added for the REM cycle. CREATE TABLE IF NOT EXISTS
    is safe to run repeatedly. Returns True if tables were created.
    """
    db_full_path = Path(os.path.expanduser(db_path))
    if not db_full_path.exists():
        return False

    async with aiosqlite.connect(db_full_path) as conn:
        # Check if lesson_versions already exists
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lesson_versions'"
        )
        already_exists = await cursor.fetchone()

        if already_exists:
            logger.info("Version tables already exist")
            return False

        # Create all three tables from the SCHEMA constant
        # (which includes them via CREATE TABLE IF NOT EXISTS)
        await conn.executescript(SCHEMA)
        await conn.commit()
        logger.info("Created lesson_versions, context_history, and rem_state tables")
        return True


async def backfill_lesson_versions(db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Create initial version records for all existing lessons.

    For lessons at v1: insert one record from current state.
    For lessons at v2+: insert current state as latest version,
    then parse [vN] annotations from rationale to reconstruct
    refinement reasons (old action text is lost).

    Idempotent: skips lessons that already have version records.
    """
    store = LessonStore(db_path)
    lessons = await store.get_all_lessons()

    db_full_path = Path(os.path.expanduser(db_path))
    inserted = 0

    async with aiosqlite.connect(db_full_path) as conn:
        conn.row_factory = aiosqlite.Row

        for lesson in lessons:
            # Check if already backfilled
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM lesson_versions WHERE lesson_id = ?",
                (lesson.id,),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] > 0:
                continue

            # Always insert a v1 record with current state as baseline
            await conn.execute(
                """
                INSERT INTO lesson_versions (
                    lesson_id, version, trigger, action, rationale, tags,
                    timestamp, refinement_reason, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lesson.id,
                    1,
                    lesson.trigger,
                    lesson.action,  # For v1, current action is all we have
                    lesson.rationale.split("\n\n[v")[0] if lesson.rationale else None,
                    json.dumps(lesson.tags),
                    lesson.created_at.isoformat(),
                    None,
                    None,
                ),
            )
            inserted += 1

            # For refined lessons, parse [vN] annotations for refinement reasons
            if lesson.version > 1 and lesson.rationale:
                version_pattern = re.compile(r'\[v(\d+)\]\s*(.*?)(?=\n\n\[v|\Z)', re.DOTALL)
                for match in version_pattern.finditer(lesson.rationale):
                    ver_num = int(match.group(1))
                    reason = match.group(2).strip()
                    if ver_num > 1:
                        await conn.execute(
                            """
                            INSERT OR IGNORE INTO lesson_versions (
                                lesson_id, version, trigger, action, rationale, tags,
                                timestamp, refinement_reason, session_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                lesson.id,
                                ver_num,
                                lesson.trigger,
                                lesson.action,  # Action text at this version is lost
                                lesson.rationale,
                                json.dumps(lesson.tags),
                                lesson.last_refined.isoformat(),
                                reason,
                                None,
                            ),
                        )
                        inserted += 1

        await conn.commit()

    logger.info(f"Backfilled {inserted} lesson version records")
    return inserted


async def seed_context_history(db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Create one initial history record for each existing project context.

    Gives every project a starting point in history. Future saves
    append real session-by-session records.

    Idempotent: skips projects that already have history.
    """
    db_full_path = Path(os.path.expanduser(db_path))
    if not db_full_path.exists():
        return 0

    inserted = 0

    async with aiosqlite.connect(db_full_path) as conn:
        conn.row_factory = aiosqlite.Row

        cursor = await conn.execute("SELECT * FROM project_contexts")
        projects = await cursor.fetchall()

        for proj in projects:
            # Check if already seeded
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM context_history WHERE project_id = ?",
                (proj["project_id"],),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] > 0:
                continue

            catalogue_json = proj["catalogue"] or "{}"
            catalogue_hash = hashlib.sha256(catalogue_json.encode()).hexdigest()

            await conn.execute(
                """
                INSERT INTO context_history (
                    project_id, session_number, timestamp, notes,
                    active_files, todos, recent_decisions,
                    catalogue_hash, catalogue_delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proj["project_id"],
                    proj["session_count"] or 0,
                    proj["last_accessed"] or datetime.now(UTC).isoformat(),
                    proj["notes"],
                    proj["active_files"] or "[]",
                    proj["todos"] or "[]",
                    proj["recent_decisions"] or "[]",
                    catalogue_hash,
                    None,  # No delta for initial snapshot
                ),
            )
            inserted += 1

        await conn.commit()

    logger.info(f"Seeded {inserted} context history records")
    return inserted


async def run_all_migrations(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Run all pending migrations."""
    results = {}

    logger.info("Starting migrations...")

    # Migration 1: Convert related_ids to relationships
    results["related_ids_migrated"] = await migrate_related_ids_to_relationships(db_path)

    # Migration 2: Ensure bidirectional relationships
    results["bidirectional_added"] = await ensure_bidirectional_relationships(db_path)

    # Migration 3: Deduplicate project contexts
    results["projects_deduplicated"] = await deduplicate_project_contexts(db_path)

    # Migration 4: Ensure unique project_path constraint
    results["unique_path_added"] = await ensure_unique_project_path(db_path)

    # Migration 5: Create version history tables (REM cycle)
    results["version_tables_created"] = await ensure_version_tables(db_path)

    # Migration 6: Backfill lesson versions from existing data
    results["lesson_versions_backfilled"] = await backfill_lesson_versions(db_path)

    # Migration 7: Seed context history from existing projects
    results["context_history_seeded"] = await seed_context_history(db_path)

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
