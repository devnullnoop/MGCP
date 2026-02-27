"""CLI for MGCP skill compilation.

Usage:
    mgcp-compile-skills compile --community ID [--all] [--force] [--dry-run] [--skills-dir PATH]
    mgcp-compile-skills list
    mgcp-compile-skills status
    mgcp-compile-skills ungraduate SKILL_NAME
"""

import argparse
import asyncio
import sys

from .graph import LessonGraph
from .persistence import LessonStore
from .qdrant_vector_store import QdrantVectorStore
from .skill_compiler import (
    compile_community,
    detect_drift,
    list_compilation_candidates,
    ungraduate_lessons,
)


async def _init():
    """Initialize stores and graph."""
    from qdrant_client import QdrantClient

    from .qdrant_vector_store import get_default_qdrant_path

    store = LessonStore()
    qdrant_path = get_default_qdrant_path()
    client = QdrantClient(path=qdrant_path)
    vector_store = QdrantVectorStore(client=client)
    graph = LessonGraph()

    lessons = await store.get_all_lessons()
    graph.load_from_lessons(lessons)

    return store, vector_store, graph


async def cmd_compile(args):
    """Compile a community or all candidates into skills."""
    store, vector_store, graph = await _init()

    if args.all:
        candidates = await list_compilation_candidates(
            store=store, graph=graph, min_maturity=0.5
        )
        if not candidates:
            print("No communities ready for compilation.")
            return

        for candidate in candidates:
            print(f"\nCompiling: {candidate.title} ({candidate.community_id})")
            result = await compile_community(
                community_id=candidate.community_id,
                store=store,
                vector_store=vector_store,
                graph=graph,
                force=args.force,
                dry_run=args.dry_run,
                skills_dir=args.skills_dir,
            )
            if "error" in result:
                print(f"  Error: {result['error']}")
            elif args.dry_run:
                print(f"  [dry-run] Would write: {result['skill_path']}")
                print(f"  Members: {result['member_count']}")
            else:
                print(f"  Compiled: {result['skill_name']} v{result['version']}")
                print(f"  Path: {result['skill_path']}")
                print(f"  Graduated: {result['graduated_count']} lessons")
    else:
        if not args.community:
            print("Error: --community ID is required (or use --all)")
            sys.exit(1)

        result = await compile_community(
            community_id=args.community,
            store=store,
            vector_store=vector_store,
            graph=graph,
            skill_name=args.name,
            skills_dir=args.skills_dir,
            force=args.force,
            dry_run=args.dry_run,
        )

        if "error" in result:
            print(f"Error: {result['error']}")
            if "assessment" in result:
                print(f"Score: {result['assessment']['score']:.0%}")
                for r in result["assessment"]["reasons"]:
                    print(f"  - {r}")
            sys.exit(1)

        if args.dry_run:
            print("Dry run — no files written:")
            print(f"  Skill: {result['skill_name']}")
            print(f"  Path: {result['skill_path']}")
            print(f"  Members: {result['member_count']}")
            print(f"\nPreview:\n{result.get('skill_body_preview', '')}")
        else:
            print(f"Compiled: {result['skill_name']} v{result['version']}")
            print(f"Path: {result['skill_path']}")
            print(f"Graduated: {result['graduated_count']} lessons")


async def cmd_list(args):
    """List all compiled skills."""
    store, vector_store, graph = await _init()

    skills = await store.get_all_compiled_skills()
    if not skills:
        print("No compiled skills.")
        return

    drift_items = await detect_drift(store)
    drift_by_skill: dict[str, int] = {}
    for item in drift_items:
        drift_by_skill[item["skill_name"]] = drift_by_skill.get(item["skill_name"], 0) + 1

    for skill in skills:
        drift = drift_by_skill.get(skill.skill_name, 0)
        status = f"DRIFT ({drift})" if drift else "OK"
        print(f"{skill.skill_name} v{skill.version} [{status}]")
        print(f"  Path: {skill.skill_path}")
        print(f"  Members: {len(skill.member_ids)}")
        print(f"  Compiled: {skill.compiled_at.strftime('%Y-%m-%d %H:%M')}")
        print()


async def cmd_status(args):
    """Show compilation candidates and drift status."""
    store, vector_store, graph = await _init()

    # Candidates
    candidates = await list_compilation_candidates(store=store, graph=graph)
    if candidates:
        print("=== Compilation Candidates ===\n")
        for c in candidates:
            print(f"  {c.title} ({c.community_id})")
            print(f"    Score: {c.score:.0%} | Members: {c.member_count} | Usage: {c.aggregate_usage}")
            for r in c.reasons:
                print(f"    - {r}")
            print()
    else:
        print("No communities ready for compilation.\n")

    # Drift
    drift_items = await detect_drift(store)
    if drift_items:
        print("=== Drift Detected ===\n")
        for item in drift_items:
            print(f"  [{item['skill_name']}] {item['lesson_id']}: {item['reason']}")
        print()
    else:
        print("No drift detected.\n")


async def cmd_ungraduate(args):
    """Reverse graduation for a skill."""
    store, vector_store, graph = await _init()

    skill = await store.get_compiled_skill(args.skill_name)
    if not skill:
        print(f"Skill not found: {args.skill_name}")
        sys.exit(1)

    count = await ungraduate_lessons(args.skill_name, store, vector_store)
    await store.delete_compiled_skill(args.skill_name)
    print(f"Ungraduated {count} lessons from '{args.skill_name}'")
    print(f"Skill file at {skill.skill_path} was NOT deleted.")


def main():
    parser = argparse.ArgumentParser(
        prog="mgcp-compile-skills",
        description="Compile MGCP lesson communities into Claude Code skills",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # compile
    compile_parser = subparsers.add_parser("compile", help="Compile a community into a skill")
    compile_parser.add_argument("--community", "-c", help="Community ID to compile")
    compile_parser.add_argument("--all", action="store_true", help="Compile all ready candidates")
    compile_parser.add_argument("--name", "-n", help="Override skill name")
    compile_parser.add_argument("--skills-dir", "-d", help="Override skills directory")
    compile_parser.add_argument("--force", "-f", action="store_true", help="Skip maturity check")
    compile_parser.add_argument("--dry-run", action="store_true", help="Preview without writing")

    # list
    subparsers.add_parser("list", help="List compiled skills")

    # status
    subparsers.add_parser("status", help="Show candidates and drift")

    # ungraduate
    ungrad_parser = subparsers.add_parser("ungraduate", help="Reverse graduation")
    ungrad_parser.add_argument("skill_name", help="Skill name to ungraduate")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "compile":
        asyncio.run(cmd_compile(args))
    elif args.command == "list":
        asyncio.run(cmd_list(args))
    elif args.command == "status":
        asyncio.run(cmd_status(args))
    elif args.command == "ungraduate":
        asyncio.run(cmd_ungraduate(args))


if __name__ == "__main__":
    main()
