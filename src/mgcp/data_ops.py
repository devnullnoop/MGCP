"""Data operations for MGCP - export, import, and maintenance."""

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from .models import Lesson
from .persistence import LessonStore
from .qdrant_vector_store import QdrantVectorStore

logger = logging.getLogger("mgcp.data_ops")


async def export_lessons(output_path: Path | None = None, include_usage: bool = True) -> dict:
    """
    Export all lessons to JSON format.

    Args:
        output_path: Path to write JSON file (None for stdout)
        include_usage: Include usage statistics in export

    Returns:
        Dict with export results
    """
    store = LessonStore()
    lessons = await store.get_all_lessons()

    export_data = {
        "mgcp_version": "1.1.0",
        "export_date": datetime.now(UTC).isoformat(),
        "lesson_count": len(lessons),
        "lessons": []
    }

    for lesson in lessons:
        lesson_dict = {
            "id": lesson.id,
            "trigger": lesson.trigger,
            "action": lesson.action,
            "rationale": lesson.rationale,
            "examples": [{"label": e.label, "code": e.code, "explanation": e.explanation} for e in lesson.examples],
            "tags": lesson.tags,
            "parent_id": lesson.parent_id,
            "relationships": [{"target": r.target, "type": r.type} for r in lesson.relationships],
            "version": lesson.version,
            "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
        }

        if include_usage:
            lesson_dict["usage_count"] = lesson.usage_count
            lesson_dict["last_used"] = lesson.last_used.isoformat() if lesson.last_used else None

        export_data["lessons"].append(lesson_dict)

    if output_path:
        output_path.write_text(json.dumps(export_data, indent=2))
        return {"status": "success", "path": str(output_path), "count": len(lessons)}
    else:
        print(json.dumps(export_data, indent=2))
        return {"status": "success", "count": len(lessons)}


async def import_lessons(
    input_path: Path,
    merge_strategy: str = "skip",  # skip, overwrite, rename
    dry_run: bool = False
) -> dict:
    """
    Import lessons from JSON file.

    Args:
        input_path: Path to JSON file
        merge_strategy: How to handle duplicates (skip, overwrite, rename)
        dry_run: If True, don't actually import

    Returns:
        Dict with import results
    """
    store = LessonStore()
    vector_store = QdrantVectorStore()

    # Load import file with error handling
    try:
        data = json.loads(input_path.read_text())
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON file {input_path}: {e}")
        return {
            "total": 0,
            "imported": 0,
            "skipped": 0,
            "overwritten": 0,
            "renamed": 0,
            "errors": [f"Invalid JSON file: {e}"],
            "dry_run": dry_run
        }

    lessons_data = data.get("lessons", [])

    # Get existing lesson IDs
    existing = await store.get_all_lessons()
    existing_ids = {l.id for l in existing}
    existing_triggers = {l.trigger.lower(): l.id for l in existing}

    results = {
        "total": len(lessons_data),
        "imported": 0,
        "skipped": 0,
        "overwritten": 0,
        "renamed": 0,
        "errors": [],
        "dry_run": dry_run
    }

    for lesson_data in lessons_data:
        try:
            lesson_id = lesson_data.get("id", "unknown")
            trigger = lesson_data.get("trigger", "")

            # Validate required fields
            if not lesson_data.get("id") or not lesson_data.get("trigger") or not lesson_data.get("action"):
                results["errors"].append(f"Lesson {lesson_id}: missing required fields (id, trigger, action)")
                continue
            # Check for duplicates
            is_duplicate_id = lesson_id in existing_ids
            is_duplicate_trigger = trigger.lower() in existing_triggers

            if is_duplicate_id or is_duplicate_trigger:
                if merge_strategy == "skip":
                    results["skipped"] += 1
                    continue
                elif merge_strategy == "overwrite":
                    if not dry_run:
                        # Delete existing and re-add
                        if is_duplicate_id:
                            await store.delete_lesson(lesson_id)
                        elif is_duplicate_trigger:
                            await store.delete_lesson(existing_triggers[trigger.lower()])
                    results["overwritten"] += 1
                elif merge_strategy == "rename":
                    # Generate new ID
                    import uuid
                    lesson_data["id"] = str(uuid.uuid4())[:8]
                    results["renamed"] += 1

            if not dry_run:
                # Create Lesson object
                from .models import Example, Relationship

                lesson = Lesson(
                    id=lesson_data["id"],
                    trigger=lesson_data["trigger"],
                    action=lesson_data["action"],
                    rationale=lesson_data.get("rationale"),
                    examples=[Example(**e) for e in lesson_data.get("examples", [])],
                    tags=lesson_data.get("tags", []),
                    parent_id=lesson_data.get("parent_id"),
                    relationships=[Relationship(**r) for r in lesson_data.get("relationships", [])],
                    version=lesson_data.get("version", 1),
                )

                # Save to store
                await store.add_lesson(lesson)

                # Add to vector store
                vector_store.add_lesson(lesson)

            results["imported"] += 1

        except Exception as e:
            results["errors"].append(f"Failed to import {lesson_id}: {e}")

    return results


async def export_projects(output_path: Path | None = None) -> dict:
    """Export all project contexts to JSON."""
    store = LessonStore()
    contexts = await store.get_all_project_contexts()

    export_data = {
        "mgcp_version": "1.1.0",
        "export_date": datetime.now(UTC).isoformat(),
        "project_count": len(contexts),
        "projects": []
    }

    for ctx in contexts:
        export_data["projects"].append({
            "project_id": ctx.project_id,
            "project_name": ctx.project_name,
            "project_path": ctx.project_path,
            "todos": [t.model_dump() for t in ctx.todos],
            "active_files": ctx.active_files,
            "recent_decisions": ctx.recent_decisions,
            "notes": ctx.notes,
            "catalogue": ctx.catalogue.model_dump() if ctx.catalogue else {},
        })

    if output_path:
        output_path.write_text(json.dumps(export_data, indent=2, default=str))
        return {"status": "success", "path": str(output_path), "count": len(contexts)}
    else:
        print(json.dumps(export_data, indent=2, default=str))
        return {"status": "success", "count": len(contexts)}


async def find_duplicates(threshold: float = 0.85) -> list[dict]:
    """
    Find potentially duplicate lessons using semantic similarity.

    Args:
        threshold: Similarity threshold (0-1) for considering duplicates

    Returns:
        List of duplicate pairs with similarity scores
    """
    store = LessonStore()
    vector_store = QdrantVectorStore()

    lessons = await store.get_all_lessons()
    lessons_by_id = {l.id: l for l in lessons}
    duplicates = []

    # Compare each lesson against others
    checked = set()
    for lesson in lessons:
        if lesson.id in checked:
            continue

        # Search for similar lessons (returns list of (id, score) tuples)
        similar = vector_store.search(
            f"{lesson.trigger} {lesson.action}",
            limit=5,
            min_score=threshold
        )

        for match_id, score in similar:
            if match_id == lesson.id:
                continue
            if match_id in checked:
                continue
            if score >= threshold:
                match_lesson = lessons_by_id.get(match_id)
                duplicates.append({
                    "lesson_1": {
                        "id": lesson.id,
                        "trigger": lesson.trigger[:50]
                    },
                    "lesson_2": {
                        "id": match_id,
                        "trigger": match_lesson.trigger[:50] if match_lesson else ""
                    },
                    "similarity": round(score, 3)
                })

        checked.add(lesson.id)

    # Sort by similarity descending
    duplicates.sort(key=lambda x: x["similarity"], reverse=True)
    return duplicates


async def suggest_tags(lesson_id: str, max_tags: int = 5) -> list[str]:
    """
    Suggest tags for a lesson based on content and similar lessons.

    Args:
        lesson_id: The lesson to suggest tags for
        max_tags: Maximum number of tags to suggest

    Returns:
        List of suggested tags
    """
    store = LessonStore()
    vector_store = QdrantVectorStore()

    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return []

    # Find similar lessons (returns list of (id, score) tuples)
    similar = vector_store.search(
        f"{lesson.trigger} {lesson.action}",
        limit=10
    )

    # Collect tags from similar lessons
    tag_counts = {}
    for match_id, score in similar:
        if match_id == lesson_id:
            continue
        similar_lesson = await store.get_lesson(match_id)
        if similar_lesson:
            for tag in similar_lesson.tags:
                # Weight by similarity
                tag_counts[tag] = tag_counts.get(tag, 0) + score

    # Filter out tags the lesson already has
    existing_tags = set(lesson.tags)
    suggestions = [
        tag for tag, _ in sorted(tag_counts.items(), key=lambda x: -x[1])
        if tag not in existing_tags
    ]

    return suggestions[:max_tags]


def main_export():
    """CLI entry point for mgcp-export."""
    import argparse

    parser = argparse.ArgumentParser(description="Export MGCP data")
    parser.add_argument(
        "type",
        choices=["lessons", "projects", "all"],
        help="What to export"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--no-usage",
        action="store_true",
        help="Exclude usage statistics from export"
    )

    args = parser.parse_args()

    async def run():
        if args.type in ["lessons", "all"]:
            output = args.output
            if args.type == "all" and args.output:
                output = args.output.with_suffix(".lessons.json")
            result = await export_lessons(output, include_usage=not args.no_usage)
            if args.output:
                print(f"Exported {result['count']} lessons to {result['path']}")

        if args.type in ["projects", "all"]:
            output = args.output
            if args.type == "all" and args.output:
                output = args.output.with_suffix(".projects.json")
            result = await export_projects(output)
            if args.output:
                print(f"Exported {result['count']} projects to {result['path']}")

    asyncio.run(run())


def main_import():
    """CLI entry point for mgcp-import."""
    import argparse

    parser = argparse.ArgumentParser(description="Import MGCP data")
    parser.add_argument(
        "file",
        type=Path,
        help="JSON file to import"
    )
    parser.add_argument(
        "--merge",
        choices=["skip", "overwrite", "rename"],
        default="skip",
        help="How to handle duplicates (default: skip)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without making changes"
    )

    args = parser.parse_args()

    if not args.file.exists():
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    async def run():
        result = await import_lessons(args.file, args.merge, args.dry_run)

        print(f"\nImport {'(dry run) ' if result['dry_run'] else ''}results:")
        print(f"  Total in file: {result['total']}")
        print(f"  Imported: {result['imported']}")
        print(f"  Skipped (duplicate): {result['skipped']}")
        print(f"  Overwritten: {result['overwritten']}")
        print(f"  Renamed: {result['renamed']}")

        if result['errors']:
            print("\n  Errors:")
            for e in result['errors']:
                print(f"    - {e}")

    asyncio.run(run())


def main_duplicates():
    """CLI entry point for finding duplicates."""
    import argparse

    parser = argparse.ArgumentParser(description="Find duplicate lessons")
    parser.add_argument(
        "-t", "--threshold",
        type=float,
        default=0.85,
        help="Similarity threshold (0-1, default: 0.85)"
    )

    args = parser.parse_args()

    async def run():
        print(f"Searching for duplicates (threshold: {args.threshold})...\n")
        duplicates = await find_duplicates(args.threshold)

        if not duplicates:
            print("No duplicates found.")
            return

        print(f"Found {len(duplicates)} potential duplicates:\n")
        for dup in duplicates:
            print(f"  Similarity: {dup['similarity']}")
            print(f"    1: [{dup['lesson_1']['id']}] {dup['lesson_1']['trigger']}")
            print(f"    2: [{dup['lesson_2']['id']}] {dup['lesson_2']['trigger']}")
            print()

    asyncio.run(run())
