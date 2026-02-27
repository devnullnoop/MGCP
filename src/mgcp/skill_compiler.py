"""Skill compilation engine for MGCP.

Compiles mature lesson communities into Claude Code skills,
graduating source lessons out of active search results.

Lifecycle:
  Discovery (MGCP) -> Maturation (community stabilizes) ->
  Compilation (generate skill) -> Graduation (prune MGCP items) ->
  Maintenance (REM detects drift, triggers recompilation)
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .models import CompiledSkill, Lesson
from .persistence import LessonStore
from .qdrant_vector_store import QdrantVectorStore

logger = logging.getLogger("mgcp.skill_compiler")

# Default maturity thresholds
MIN_COMMUNITY_SIZE = 4
MIN_AGGREGATE_USAGE = 10
MIN_STABLE_CYCLES = 2

DEFAULT_SKILLS_DIR = os.path.expanduser("~/.claude/skills")


def _get_skills_dir(skills_dir: str | None = None) -> Path:
    """Resolve skills directory from arg, env, or default."""
    if skills_dir:
        return Path(skills_dir)
    env_dir = os.environ.get("MGCP_SKILLS_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(DEFAULT_SKILLS_DIR)


@dataclass
class MaturityAssessment:
    """Result of assessing a community's readiness for compilation."""

    score: float  # 0.0 to 1.0
    ready: bool
    reasons: list[str] = field(default_factory=list)
    community_id: str = ""
    title: str = ""
    member_count: int = 0
    aggregate_usage: int = 0


def assess_maturity(
    community: dict,
    lessons: list[Lesson],
    store: LessonStore | None = None,
    summary_exists: bool = False,
) -> MaturityAssessment:
    """Score a community's readiness for compilation.

    Criteria (each contributes to score):
    - Size >= MIN_COMMUNITY_SIZE (required)
    - Aggregate usage >= MIN_AGGREGATE_USAGE
    - Has a community summary
    - Members are not already graduated

    Args:
        community: Community dict from graph.detect_communities()
        lessons: The lesson objects for this community's members
        store: Optional store (unused currently, reserved for stable-cycle check)
        summary_exists: Whether a community summary exists for this community
    """
    reasons = []
    score = 0.0
    member_count = len(lessons)
    aggregate_usage = sum(l.usage_count for l in lessons)

    # Size check (required gate)
    if member_count < MIN_COMMUNITY_SIZE:
        reasons.append(f"Too small: {member_count} lessons (need {MIN_COMMUNITY_SIZE}+)")
        return MaturityAssessment(
            score=0.0,
            ready=False,
            reasons=reasons,
            community_id=community.get("community_id", ""),
            title=community.get("summary_title", "Untitled"),
            member_count=member_count,
            aggregate_usage=aggregate_usage,
        )

    # Size contribution (0.2 max)
    score += min(member_count / 8.0, 1.0) * 0.2
    reasons.append(f"Size: {member_count} lessons")

    # Usage contribution (0.3 max)
    if aggregate_usage >= MIN_AGGREGATE_USAGE:
        score += min(aggregate_usage / 30.0, 1.0) * 0.3
        reasons.append(f"Usage: {aggregate_usage} aggregate retrievals")
    else:
        reasons.append(f"Low usage: {aggregate_usage} (need {MIN_AGGREGATE_USAGE}+)")

    # Summary exists (0.2)
    if summary_exists:
        score += 0.2
        reasons.append("Has community summary")
    else:
        reasons.append("No community summary yet")

    # No graduated members (0.15)
    graduated = [l for l in lessons if l.graduated_to]
    if not graduated:
        score += 0.15
        reasons.append("No members already graduated")
    else:
        reasons.append(f"{len(graduated)} members already graduated")

    # Average version > 1 indicates refinement (0.15)
    avg_version = sum(l.version for l in lessons) / max(len(lessons), 1)
    if avg_version > 1.0:
        score += min((avg_version - 1.0) / 2.0, 1.0) * 0.15
        reasons.append(f"Average version: {avg_version:.1f} (refined)")
    else:
        reasons.append("Members haven't been refined")

    ready = score >= 0.5

    return MaturityAssessment(
        score=round(score, 2),
        ready=ready,
        reasons=reasons,
        community_id=community.get("community_id", ""),
        title=community.get("summary_title", "Untitled"),
        member_count=member_count,
        aggregate_usage=aggregate_usage,
    )


def generate_skill_description(lessons: list[Lesson]) -> str:
    """Synthesize a skill description from aggregate lesson triggers.

    Deduplicates and condenses trigger keywords into a short description
    suitable for the skill's YAML frontmatter description field.
    """
    # Collect all trigger keywords
    all_triggers = set()
    for lesson in lessons:
        # Split on common delimiters
        for part in lesson.trigger.replace(",", " ").replace(";", " ").split():
            cleaned = part.strip().lower()
            if cleaned and len(cleaned) > 2:
                all_triggers.add(cleaned)

    # Sort by frequency across lessons
    trigger_counts: dict[str, int] = {}
    for lesson in lessons:
        trigger_lower = lesson.trigger.lower()
        for t in all_triggers:
            if t in trigger_lower:
                trigger_counts[t] = trigger_counts.get(t, 0) + 1

    # Take top keywords
    sorted_triggers = sorted(trigger_counts.items(), key=lambda x: x[1], reverse=True)
    top = [t for t, _ in sorted_triggers[:10]]

    return ", ".join(top) if top else "general guidance"


def generate_skill_body(
    title: str,
    summary: str,
    lessons: list[Lesson],
    community_id: str,
    skill_name: str,
) -> str:
    """Generate the full SKILL.md content from community data.

    Produces a structured skill file with YAML frontmatter and
    distilled practices (NOT a 1:1 lesson dump).
    """
    description = generate_skill_description(lessons)
    now = datetime.now(UTC).strftime("%Y-%m-%d")

    # Group lessons by tags for thematic sections
    tag_groups: dict[str, list[Lesson]] = {}
    ungrouped: list[Lesson] = []
    for lesson in lessons:
        if lesson.tags:
            primary_tag = lesson.tags[0]
            tag_groups.setdefault(primary_tag, []).append(lesson)
        else:
            ungrouped.append(lesson)

    # Build key practices section
    practices_lines = []
    for tag, group in sorted(tag_groups.items()):
        practices_lines.append(f"\n### {tag.replace('-', ' ').title()}")
        for lesson in group:
            practices_lines.append(f"- {lesson.action}")
    if ungrouped:
        practices_lines.append("\n### General")
        for lesson in ungrouped:
            practices_lines.append(f"- {lesson.action}")

    practices = "\n".join(practices_lines)

    # Build trigger patterns section
    trigger_lines = []
    for lesson in lessons:
        trigger_lines.append(f"- {lesson.trigger}")
    triggers = "\n".join(trigger_lines)

    # Build common pitfalls from bad examples
    pitfall_lines = []
    for lesson in lessons:
        for ex in lesson.examples:
            if ex.label == "bad":
                explanation = f" — {ex.explanation}" if ex.explanation else ""
                pitfall_lines.append(f"- `{ex.code}`{explanation}")
    pitfalls = "\n".join(pitfall_lines) if pitfall_lines else "No common pitfalls documented yet."

    body = f"""---
name: {skill_name}
description: "{description}"
user-invocable: false
---

# {title}

{summary}

## Key Practices
{practices}

## When This Applies
{triggers}

## Common Pitfalls
{pitfalls}

---
*Compiled from MGCP community {community_id} ({len(lessons)} lessons) on {now}.*
"""
    return body


async def compile_community(
    community_id: str,
    store: LessonStore,
    vector_store: QdrantVectorStore,
    graph,
    skill_name: str | None = None,
    skills_dir: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Full compilation pipeline for a community.

    Steps: assess maturity -> generate skill -> write file -> graduate lessons -> track.

    Args:
        community_id: The community to compile
        store: Lesson store
        vector_store: Vector store for updating payloads
        graph: LessonGraph for community detection
        skill_name: Override skill name (default: derived from community title)
        skills_dir: Override skills directory
        force: Skip maturity check
        dry_run: Preview without writing files or graduating

    Returns:
        Dict with compilation result details
    """
    # Find the community
    communities = graph.detect_communities()
    target = None
    for c in communities:
        if c["community_id"] == community_id:
            target = c
            break

    if not target:
        return {"error": f"Community '{community_id}' not found in graph"}

    # Fetch member lessons
    lessons = []
    for member_id in target["members"]:
        lesson = await store.get_lesson(member_id)
        if lesson:
            lessons.append(lesson)

    if not lessons:
        return {"error": "No lessons found for community members"}

    # Check for community summary
    summary_obj = await store.get_community_summary(community_id)
    summary_exists = summary_obj is not None

    # Assess maturity
    assessment = assess_maturity(target, lessons, store, summary_exists)

    if not force and not assessment.ready:
        return {
            "error": "Community not ready for compilation",
            "assessment": {
                "score": assessment.score,
                "reasons": assessment.reasons,
            },
        }

    # Determine skill name
    if not skill_name:
        title = summary_obj.title if summary_obj else f"community-{community_id[:8]}"
        skill_name = title.lower().replace(" ", "-").replace("_", "-")
        # Clean up non-alphanumeric chars
        skill_name = "".join(c if c.isalnum() or c == "-" else "" for c in skill_name)
        skill_name = skill_name.strip("-")

    # Get summary text
    summary_text = summary_obj.summary if summary_obj else "Knowledge compiled from MGCP lesson community."
    title_text = summary_obj.title if summary_obj else skill_name.replace("-", " ").title()

    # Generate skill content
    skill_body = generate_skill_body(
        title=title_text,
        summary=summary_text,
        lessons=lessons,
        community_id=community_id,
        skill_name=skill_name,
    )

    # Resolve output path
    resolved_dir = _get_skills_dir(skills_dir)
    skill_dir = resolved_dir / skill_name
    skill_path = skill_dir / "SKILL.md"

    if dry_run:
        return {
            "dry_run": True,
            "skill_name": skill_name,
            "skill_path": str(skill_path),
            "member_count": len(lessons),
            "member_ids": [l.id for l in lessons],
            "assessment": {
                "score": assessment.score,
                "reasons": assessment.reasons,
            },
            "skill_body_preview": skill_body[:500] + "..." if len(skill_body) > 500 else skill_body,
        }

    # Check for existing compiled skill (recompilation)
    existing = await store.get_compiled_skill(skill_name)
    new_version = (existing.version + 1) if existing else 1

    # Write skill file
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(skill_body, encoding="utf-8")
    logger.info(f"Wrote skill file: {skill_path}")

    # Track compilation
    compiled = CompiledSkill(
        skill_name=skill_name,
        community_id=community_id,
        member_ids=[l.id for l in lessons],
        skill_path=str(skill_path),
        compiled_at=datetime.now(UTC),
        version=new_version,
    )
    await store.save_compiled_skill(compiled)

    # Graduate lessons
    graduated_count = await graduate_lessons(
        lesson_ids=[l.id for l in lessons],
        skill_name=skill_name,
        store=store,
        vector_store=vector_store,
    )

    return {
        "skill_name": skill_name,
        "skill_path": str(skill_path),
        "version": new_version,
        "member_count": len(lessons),
        "graduated_count": graduated_count,
        "community_id": community_id,
    }


async def graduate_lessons(
    lesson_ids: list[str],
    skill_name: str,
    store: LessonStore,
    vector_store: QdrantVectorStore,
) -> int:
    """Mark lessons as graduated and update Qdrant payloads.

    Returns count of lessons graduated.
    """
    count = 0
    for lesson_id in lesson_ids:
        lesson = await store.get_lesson(lesson_id)
        if not lesson:
            continue
        lesson.graduated_to = skill_name
        await store.update_lesson(lesson)

        # Update Qdrant payload to include graduation flag
        vector_store.add_lesson(lesson)
        count += 1

    logger.info(f"Graduated {count} lessons to skill '{skill_name}'")
    return count


async def ungraduate_lessons(
    skill_name: str,
    store: LessonStore,
    vector_store: QdrantVectorStore,
) -> int:
    """Reverse graduation — restore lessons to active search.

    Returns count of lessons ungraduated.
    """
    lessons = await store.get_all_lessons()
    count = 0
    for lesson in lessons:
        if lesson.graduated_to == skill_name:
            lesson.graduated_to = None
            await store.update_lesson(lesson)
            vector_store.add_lesson(lesson)
            count += 1

    logger.info(f"Ungraduated {count} lessons from skill '{skill_name}'")
    return count


async def detect_drift(
    store: LessonStore,
) -> list[dict]:
    """Find graduated lessons that were refined after compilation.

    Returns list of drift findings with skill_name, lesson_id, and reason.
    """
    skills = await store.get_all_compiled_skills()
    if not skills:
        return []

    findings = []
    for skill in skills:
        for member_id in skill.member_ids:
            lesson = await store.get_lesson(member_id)
            if not lesson:
                # Member deleted — that's drift
                findings.append({
                    "skill_name": skill.skill_name,
                    "lesson_id": member_id,
                    "reason": "deleted",
                    "compiled_at": skill.compiled_at.isoformat(),
                })
                continue

            if lesson.last_refined > skill.compiled_at:
                findings.append({
                    "skill_name": skill.skill_name,
                    "lesson_id": member_id,
                    "reason": "refined_after_compilation",
                    "compiled_at": skill.compiled_at.isoformat(),
                    "last_refined": lesson.last_refined.isoformat(),
                })

    return findings


async def list_compilation_candidates(
    store: LessonStore,
    graph,
    min_maturity: float = 0.5,
) -> list[MaturityAssessment]:
    """Find communities ready for compilation.

    Returns list of MaturityAssessments for communities meeting the threshold.
    """
    communities = graph.detect_communities()
    lessons = await store.get_all_lessons()
    lesson_map = {l.id: l for l in lessons}

    candidates = []
    for community in communities:
        member_lessons = [
            lesson_map[mid] for mid in community.get("members", [])
            if mid in lesson_map
        ]
        if not member_lessons:
            continue

        summary = await store.get_community_summary(community.get("community_id", ""))
        assessment = assess_maturity(
            community, member_lessons, store, summary_exists=summary is not None
        )

        if assessment.score >= min_maturity:
            candidates.append(assessment)

    candidates.sort(key=lambda a: a.score, reverse=True)
    return candidates
