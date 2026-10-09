"""Data operations for MGCP - export, import, and maintenance."""

import asyncio
import itertools
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
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
        "mgcp_version": __version__,
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

    if "lessons" not in data and "projects" in data:
        return {
            "total": 0, "imported": 0, "skipped": 0, "overwritten": 0, "renamed": 0,
            "errors": [
                "This is a project-context export, and mgcp-import only imports "
                "lessons. Restore project contexts and catalogues with "
                "mgcp-backup --restore."
            ],
            "dry_run": dry_run,
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
        # Bound before the try because the handler below reports on it. A file
        # whose "lessons" is a list of strings (or [null]) parses as JSON, so
        # the first .get inside the try raised and the handler then died on an
        # unbound name — or, worse, blamed the previous lesson's id.
        lesson_id = lesson_data.get("id", "unknown") if isinstance(lesson_data, dict) else "unknown"
        try:
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
                        # Delete existing and re-add. Remove the old vector
                        # too: on a trigger-duplicate the incoming id differs,
                        # so the upsert below would leave the old id's vector
                        # orphaned in Qdrant (searchable but unfetchable).
                        deleted_id = (
                            lesson_id if is_duplicate_id
                            else existing_triggers[trigger.lower()]
                        )
                        await store.delete_lesson(deleted_id)
                        vector_store.remove_vector_lesson(deleted_id)
                    results["overwritten"] += 1
                elif merge_strategy == "rename":
                    # Generate new ID
                    import uuid
                    lesson_data["id"] = str(uuid.uuid4())[:8]
                    results["renamed"] += 1

            if not dry_run:
                # Create Lesson object
                from .models import Example, Relationship

                relationships = [Relationship(**r) for r in lesson_data.get("relationships", [])]

                # Exports written before related_ids was removed carry their
                # cross-links only in that key. Pydantic would drop it
                # silently, so fold it in the way the DB migration does.
                known_targets = {r.target for r in relationships}
                relationships += [
                    Relationship(target=rid, type="related", weight=0.5,
                                 context=[], bidirectional=True)
                    for rid in lesson_data.get("related_ids", [])
                    if rid not in known_targets
                ]

                # Preserve exported timestamps/usage so a round-trip does not
                # reset every lesson to created-now/never-used, which would
                # mistrain the REM staleness scan.
                extra_fields = {}
                if lesson_data.get("created_at"):
                    extra_fields["created_at"] = datetime.fromisoformat(
                        lesson_data["created_at"]
                    )
                if lesson_data.get("usage_count") is not None:
                    extra_fields["usage_count"] = int(lesson_data["usage_count"])
                if lesson_data.get("last_used"):
                    extra_fields["last_used"] = datetime.fromisoformat(
                        lesson_data["last_used"]
                    )

                lesson = Lesson(
                    id=lesson_data["id"],
                    trigger=lesson_data["trigger"],
                    action=lesson_data["action"],
                    rationale=lesson_data.get("rationale"),
                    examples=[Example(**e) for e in lesson_data.get("examples", [])],
                    tags=lesson_data.get("tags", []),
                    parent_id=lesson_data.get("parent_id"),
                    relationships=relationships,
                    version=lesson_data.get("version", 1),
                    **extra_fields,
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
        "mgcp_version": __version__,
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


# Words that appear in so many triggers that sharing one proves nothing.
_TRIGGER_NOISE = frozenset({
    "a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "be", "it", "this", "that", "with", "when", "your", "you", "not", "do",
    "if", "as", "at", "by", "any", "use", "new", "all", "before", "after",
})

# The minimum share of trigger words two lessons must have in common.
#
# Measured on a 322-lesson corpus: 1,537 pairs share two or more trigger words
# and 26 clear 0.4, which is a list short enough to read. The two known
# duplicate pairs sit at 0.60 and 1.00.
DEFAULT_TRIGGER_OVERLAP = 0.4

# One shared word gives an overlap of 1.0 when both triggers are one word long,
# which is an artifact rather than evidence.
MIN_SHARED_TRIGGER_WORDS = 2


def trigger_words(trigger: str) -> set[str]:
    """The words a trigger can match on, minus noise and short tokens."""
    return {
        word for word in re.findall(r"[a-z0-9]+", (trigger or "").lower())
        if len(word) > 2 and word not in _TRIGGER_NOISE
    }


def trigger_overlap(a: str, b: str) -> tuple[float, list[str]]:
    """(shared share, the shared words) between two triggers.

    The share is the intersection over the union, so a pair of long triggers
    needs to agree on more words than a pair of short ones.
    """
    words_a, words_b = trigger_words(a), trigger_words(b)
    if not words_a or not words_b:
        return 0.0, []
    shared = words_a & words_b
    return len(shared) / len(words_a | words_b), sorted(shared)


REVERSE_RELATIONSHIP = {
    "prerequisite": "sequence_next",
    "sequence_next": "prerequisite",
    "specializes": "generalizes",
    "generalizes": "specializes",
}


def _append_edge(lesson: Lesson, rel) -> bool:
    """Add one typed edge to a lesson, or report it already had it.

    The same typed edge is never added twice. A different type between the
    same pair is meaningful, so it is allowed.
    """
    if (rel.target, rel.type) in {(r.target, r.type) for r in lesson.relationships}:
        return False
    lesson.relationships.append(rel)
    return True


async def link_pair(store: LessonStore, graph, source_id: str, rel) -> tuple[bool, str]:
    """Link two lessons, in SQLite and in the graph. Returns (changed, message).

    ``rel`` is a models.Relationship carrying the target and the kind of edge,
    which is what keeps this to four arguments: the five fields of an edge are
    one object and not five parameters.

    This is the one implementation. The MCP tool and the dashboard both act on
    a REM ``link_suggestions`` finding, and an unlinked lesson stays invisible
    to the community bridge until the edge exists in the graph as well as in
    SQLite, which is the half a second copy forgets.
    """
    lessons = {}
    for lesson_id in (source_id, rel.target):
        lesson = await store.get_lesson(lesson_id)
        if not lesson:
            return False, f"Lesson not found: {lesson_id}"
        lessons[lesson_id] = lesson

    edges = [(lessons[source_id], rel)]
    if rel.bidirectional:
        edges.append((lessons[rel.target], rel.model_copy(update={
            "target": source_id,
            "type": REVERSE_RELATIONSHIP.get(rel.type, rel.type),
        })))

    changed = [lesson for lesson, edge in edges if _append_edge(lesson, edge)]
    if not changed:
        return False, (f"'{source_id}' and '{rel.target}' are already linked "
                       f"({rel.type}). No change made.")
    for lesson in changed:
        await store.update_lesson(lesson)
    for lesson in lessons.values():
        graph.add_lesson(lesson)

    arrow = "↔" if rel.bidirectional else "→"
    suffix = f" ({rel.type})" if rel.type != "related" else ""
    return True, f"Linked '{source_id}' {arrow} '{rel.target}'{suffix}"


async def find_duplicates(
    min_overlap: float = DEFAULT_TRIGGER_OVERLAP,
    store: LessonStore | None = None,
    vector_store: QdrantVectorStore | None = None,
    limit: int = 25,
) -> list[dict]:
    """Lessons that compete for the same retrieval, most overlap first.

    Ranked by how much their TRIGGERS share, not by how similar their text is.
    Two lessons that fire on the same words are always returned together and one
    of them is redundant by construction. That question has an exact answer, and
    "do these two mean the same thing" does not.

    Embedding similarity cannot answer it. Measured on a 322-lesson corpus, two
    lessons carrying the same rule in different words scored 0.739 against the
    old 0.85 gate, so they were never reported and sat in the store for nine
    months. Lowering the gate would not have helped: that pair ranked 110th of
    581 candidates by similarity, behind 109 pairs that were mostly
    complementary rather than duplicate. By trigger overlap the same pair ranks
    6th of 1,537.

    ``similarity`` is reported when a vector store is given, as context for
    whoever reads the pair, and it is never the decision. No vector store means
    no similarity column and no Qdrant lock.

    Args:
        min_overlap: the least share of trigger words a reported pair has.
        store: an existing LessonStore. Required from any caller that holds one.
        vector_store: an existing QdrantVectorStore, for the similarity column
            only. Local Qdrant permits one client per path, so an in-process
            caller must pass its own or pass nothing.
        limit: how many pairs to return, after ranking.

    Returns:
        Ranked pairs, each with both lessons, the overlap, the shared words and
        an optional similarity.
    """
    store = store or LessonStore()
    lessons = await store.get_all_lessons()

    ranked = []
    for first, second in itertools.combinations(lessons, 2):
        share, shared = trigger_overlap(first.trigger, second.trigger)
        if len(shared) < MIN_SHARED_TRIGGER_WORDS or share < min_overlap:
            continue
        ranked.append((share, len(shared), first, second))
    ranked.sort(key=lambda row: (-row[0], -row[1], row[2].id))

    out = []
    for share, _count, first, second in ranked[:max(1, limit)]:
        _share, shared = trigger_overlap(first.trigger, second.trigger)
        out.append({
            "lesson_1": {"id": first.id, "trigger": first.trigger[:80],
                         "usage_count": first.usage_count},
            "lesson_2": {"id": second.id, "trigger": second.trigger[:80],
                         "usage_count": second.usage_count},
            "trigger_overlap": round(share, 3),
            "shared_words": shared,
            "similarity": _pair_similarity(vector_store, first, second),
        })
    return out


def _pair_similarity(vector_store, first, second) -> float | None:
    """Cosine similarity between two lessons, or None when it cannot be had.

    Context for the reader, never the ranking. A failure here must not hide a
    trigger collision that was already proven by the words themselves.
    """
    if vector_store is None:
        return None
    try:
        hits = dict(vector_store.search(
            f"{first.trigger} {first.action}", limit=50, min_score=0.0))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not score {first.id} against {second.id}: {exc}")
        return None
    score = hits.get(second.id)
    return round(score, 3) if score is not None else None


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
    if args.type == "all" and not args.output:
        # Both halves would print to stdout back to back, and two concatenated
        # JSON documents are not loadable by the importer that reads them.
        parser.error("--output is required when exporting 'all'")

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

    parser = argparse.ArgumentParser(
        description="Find lessons that compete for the same retrieval")
    parser.add_argument(
        "-t", "--min-overlap",
        type=float,
        default=DEFAULT_TRIGGER_OVERLAP,
        help=(f"least share of trigger words a reported pair has "
              f"(0-1, default: {DEFAULT_TRIGGER_OVERLAP})")
    )
    parser.add_argument(
        "-n", "--limit", type=int, default=25,
        help="how many pairs to report, after ranking (default: 25)"
    )

    args = parser.parse_args()

    async def run():
        print(f"Ranking trigger collisions (min overlap: {args.min_overlap})...\n")
        duplicates = await find_duplicates(args.min_overlap, limit=args.limit)

        if not duplicates:
            print(f"No pair shares {args.min_overlap:.0%} of its trigger words.")
            print("That is a statement about trigger overlap only. Two lessons "
                  "can still say the same thing in different words.")
            return

        print(f"{len(duplicates)} pair(s), most overlap first:\n")
        for dup in duplicates:
            shared = ", ".join(dup["shared_words"])
            print(f"  overlap {dup['trigger_overlap']:.0%} on: {shared}")
            print(f"    1: [{dup['lesson_1']['id']}] {dup['lesson_1']['trigger']}")
            print(f"    2: [{dup['lesson_2']['id']}] {dup['lesson_2']['trigger']}")
            print()

    asyncio.run(run())
