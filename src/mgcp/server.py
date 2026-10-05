"""MGCP - Memory Graph Core Primitives.

MCP server providing persistent, graph-based memory for LLM interactions.
Uses FastMCP for cleaner API - verified against official SDK docs.
https://github.com/modelcontextprotocol/python-sdk
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from mcp.server.fastmcp import FastMCP
from qdrant_client import QdrantClient

from .graph import LessonGraph
from .logging_config import configure_logging
from .models import (
    ArchitecturalNote,
    CommunitySummary,
    Convention,
    Decision,
    Dependency,
    ErrorPattern,
    FileCoupling,
    GenericCatalogueItem,
    Lesson,
    ProjectContext,
    ProjectTodo,
    RelationshipType,
    SecurityNote,
    Workflow,
    WorkflowStep,
    WorkflowStepLesson,
    age_phrase,
    sanitize_tool_call_xml,
)
from .persistence import LessonStore, StaleWriteError
from .qdrant_catalogue_store import QdrantCatalogueStore
from .qdrant_vector_store import QdrantVectorStore
from .telemetry import TelemetryLogger

# Configure logging with rotation (CRITICAL: logs to file, not stdout which breaks MCP STDIO)
configure_logging(console_output=False)
logger = logging.getLogger("mgcp.server")

# Initialize FastMCP server
mcp = FastMCP("mgcp")

# Global state (initialized on first tool call)
_store: LessonStore | None = None
_vector_store: QdrantVectorStore | None = None
_catalogue_vector: QdrantCatalogueStore | None = None
_qdrant_client: QdrantClient | None = None
_graph: LessonGraph | None = None
_telemetry: TelemetryLogger | None = None
_initialized = False
_init_lock = asyncio.Lock()
# Vectors initialize separately from SQLite, under their own lock, because
# embedded Qdrant can be locked by another process while SQLite is perfectly
# readable. One lock for both would make the 26 SQLite-only tools wait on, and
# fail with, a store they never touch.
_vectors_initialized = False
_vector_init_lock = asyncio.Lock()

# Validation constants
# Derived from the model's Literal so the two cannot drift; the Literal itself
# now carries the note about why hierarchy is not a relationship type.
VALID_RELATIONSHIP_TYPES = frozenset(get_args(RelationshipType))
# Community-bridge tuning. The pool is a single scored search whose results are
# intersected with the matched community's members; the floor is what a bridged
# lesson must score against the query to be worth injecting at all.
BRIDGE_POOL_SIZE = 60
# 0.55, from a sweep over the 34 labelled queries (docs/bridge-measurement.md,
# reproduce with `python -m tests.bridge_benchmark --sweep`). The floor was
# 0.25, and every value from 0.25 to 0.45 produced an identical 30 appends, so
# 0.30 of that range did nothing at all: the bridge's candidates all score
# above 0.45. 0.55 is the first value that changes anything, and it drops 4
# appends while keeping the one append a label vouches for. 0.60 drops that
# one too. The four it discards are unlabelled rather than known-useless, so
# this buys a smaller context window on thin evidence; it is not a claim that
# they were worthless.
BRIDGE_MIN_SCORE = 0.55
LESSON_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$|^[a-z0-9]$")
MAX_LESSON_ID_LENGTH = 100


def _validate_lesson_id(lesson_id: str) -> str | None:
    """Validate lesson ID format. Returns error message or None if valid."""
    if not lesson_id:
        return "Lesson ID cannot be empty"
    if len(lesson_id) > MAX_LESSON_ID_LENGTH:
        return f"Lesson ID too long (max {MAX_LESSON_ID_LENGTH} characters)"
    if not LESSON_ID_PATTERN.match(lesson_id):
        return "Lesson ID must be lowercase with dashes (e.g., 'my-lesson-id')"
    return None


def _validate_relationship_type(rel_type: str) -> str | None:
    """Validate relationship type. Returns error message or None if valid."""
    if rel_type not in VALID_RELATIONSHIP_TYPES:
        valid = ", ".join(sorted(VALID_RELATIONSHIP_TYPES))
        return f"Invalid relationship type '{rel_type}'. Valid types: {valid}"
    return None


class VectorStoreUnavailableError(RuntimeError):
    """Qdrant could not be opened, so semantic search is unavailable.

    Carries an actionable message: which process holds the embedded lock, and
    the one environment variable that removes the restriction entirely.
    """


def _describe_lock_holder() -> str:
    """Name the process holding the embedded Qdrant lock, if it can be found.

    Best-effort and never raises: this runs only on an error path, and a
    diagnostic that can fail the call it is diagnosing is worse than no
    diagnostic. `lsof` is absent on some systems and may be slow on others,
    hence the short timeout.
    """
    from .qdrant_vector_store import get_default_qdrant_path, get_qdrant_url

    if get_qdrant_url():
        return ""
    lock_file = Path(get_default_qdrant_path()) / ".lock"
    if not lock_file.exists():
        return ""
    try:
        import subprocess

        pids = subprocess.run(
            ["lsof", "-t", str(lock_file)],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.split()
        # Never report ourselves. A failed open leaves this process holding the
        # lock file briefly, so lsof lists it. A message naming the caller as
        # the culprit points at the wrong process to kill.
        own = str(os.getpid())
        pids = [pid for pid in pids if pid != own]
        if not pids:
            return ""
        described = []
        for pid in pids[:3]:
            cmd = subprocess.run(
                ["ps", "-o", "command=", "-p", pid],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip()
            # The executable's absolute path is noise; the tail identifies it.
            if cmd:
                short = " ".join(Path(part).name for part in cmd.split()[:3])
                described.append(f"PID {pid} ({short})")
            else:
                described.append(f"PID {pid}")
        return " Currently held by: " + "; ".join(described) + "."
    except Exception:  # pragma: no cover - diagnostics are never load-bearing
        return ""


async def _ensure_initialized() -> tuple[LessonStore, LessonGraph, TelemetryLogger]:
    """Open the SQLite-backed components. Never touches Qdrant.

    Split from the vector stores deliberately. Embedded Qdrant permits one
    client per path, so when another MGCP process holds the lock this used to
    raise from a single all-or-nothing try block and take down all 50 tools.
    That included `read_soliloquy`, `get_project_context`, `save_project_context`
    and `write_soliloquy`, none of which need a vector at all. A session that
    started while a previous session's server was still alive could therefore
    neither load its memory nor save it. 26 of the 36 tools need SQLite only;
    they now work regardless of the lock, and the 10 that need vectors say what
    is holding it. See `web_server.get_vector_store` for the same shape.
    """
    global _store, _graph, _telemetry, _initialized

    # Fast path: already initialized
    if _initialized:
        return _store, _graph, _telemetry

    # Slow path: acquire lock and initialize
    async with _init_lock:
        # Double-check after acquiring lock
        if _initialized:
            return _store, _graph, _telemetry

        logger.info("Initializing MGCP server (SQLite, graph, telemetry)...")

        try:
            _store = LessonStore()
            _graph = LessonGraph()
            _telemetry = TelemetryLogger()

            # Load lessons from database and build the graph
            lessons = await _store.get_all_lessons()
            logger.info(f"Loaded {len(lessons)} lessons from database")
            _graph.load_from_lessons(lessons)

            # Start telemetry session
            await _telemetry.start_session()

            _initialized = True
            logger.info("Server initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize MGCP server: {e}")
            # Reset partially-set globals so the next tool call retries from
            # scratch instead of failing forever on a half-built server.
            _store = _graph = _telemetry = None
            raise

        return _store, _graph, _telemetry


async def _ensure_vector_stores() -> tuple[QdrantVectorStore, QdrantCatalogueStore]:
    """Open Qdrant on first use by a tool that actually needs it.

    Raises VectorStoreUnavailableError, naming the lock holder, rather than letting
    a Qdrant error escape as the raw "Storage folder already accessed" string
    that tells the caller nothing it can act on.
    """
    global _vector_store, _catalogue_vector, _qdrant_client, _vectors_initialized

    if _vectors_initialized:
        return _vector_store, _catalogue_vector

    async with _vector_init_lock:
        if _vectors_initialized:
            return _vector_store, _catalogue_vector

        # SQLite is the source of truth for everything indexed below.
        store, _graph_unused, _telemetry_unused = await _ensure_initialized()

        logger.info("Opening vector stores...")
        client = None
        try:
            # If the config points at our own local server and it is not up, start
            # it. Otherwise the first session to need vectors after a reboot would
            # lose semantic search for its whole life, with a server installed and
            # idle on disk. No-op in embedded mode.
            from .qdrant_server import ensure_running_if_configured

            ensure_running_if_configured()

            # Create a single shared Qdrant client for all vector stores
            # CRITICAL: Qdrant local mode only allows ONE client per path.
            # Multiple clients cause "Storage folder already accessed" errors.
            from .qdrant_vector_store import (
                get_default_qdrant_path,
                qdrant_client_args,
            )
            qdrant_path = get_default_qdrant_path()
            client = QdrantClient(**qdrant_client_args(qdrant_path))

            _vector_store = QdrantVectorStore(client=client)
            _catalogue_vector = QdrantCatalogueStore(client=client)

            # Reconcile from SQLite. This is also the repair path for anything
            # written while the store was locked: a lesson, catalogue item or
            # community summary that could not be indexed at write time is
            # indexed here, the next time the store opens.
            lessons = await store.get_all_lessons()
            stored_ids = set(_vector_store.get_all_ids())
            missing = [le for le in lessons if le.id not in stored_ids]
            for lesson in missing:
                _vector_store.add_lesson(lesson)
            if missing:
                logger.info(f"Indexed {len(missing)} lessons missing from Qdrant")

            contexts = await store.get_all_project_contexts()
            for ctx in contexts:
                _catalogue_vector.index_catalogue(ctx.project_id, ctx.catalogue)
            logger.info(f"Indexed catalogues for {len(contexts)} projects")

            community_summaries = await store.get_all_community_summaries()
            for cs in community_summaries:
                searchable = f"Community: {cs.title}. {cs.summary}."
                _vector_store.upsert_community_summary(
                    community_id=cs.community_id,
                    searchable_text=searchable,
                    metadata={
                        "title": cs.title,
                        "member_count": cs.member_count,
                    },
                )
            if community_summaries:
                logger.info(f"Indexed {len(community_summaries)} community summaries")

            _qdrant_client = client
            _vectors_initialized = True
            logger.info("Vector stores opened successfully")

        except Exception as e:
            logger.error(f"Vector stores unavailable: {e}")
            # Release the storage lock and reset, so a later call can retry
            # once whatever holds it has exited.
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            _vector_store = _catalogue_vector = _qdrant_client = None
            raise VectorStoreUnavailableError(
                f"Semantic search is unavailable: {e}"
                f"{_describe_lock_holder()} Embedded Qdrant allows one client per "
                "path, so another MGCP process (an older session's server, or the "
                "dashboard) can hold it. Run `mgcp-qdrant setup` once to install a "
                "local Qdrant server and share one store across every session. "
                "No container, nothing to fetch by hand. Everything backed by "
                "SQLite still works: project context, the soliloquy journal, "
                "lesson reads by id, workflows, and REM."
            ) from e

        return _vector_store, _catalogue_vector


class _UnindexedWrites:
    """Accepts and drops index calls while the vector store is locked.

    Installed by `_try_vector_stores` on the degraded path only, and never
    reachable when Qdrant opened. Dropping the call is safe precisely because
    `_ensure_vector_stores` reconciles from SQLite the next time it opens: the
    row written now is indexed then. The caller is told so in its return value,
    so this degrades loudly rather than silently. A dropped write nobody is
    told about is the real failure, not the dropping itself.
    """

    def __getattr__(self, _name):
        def _drop(*_args, **_kwargs):
            return None

        return _drop


async def _try_vector_stores() -> tuple[object, object, str]:
    """Vector stores if available, else no-op indexes plus a warning.

    For writes whose SQLite row is the source of truth and whose index
    `_ensure_vector_stores` rebuilds. Refusing those would be worse than
    degrading: `add_lesson` is the apology gate's only comply exit, and a gate
    whose exit depends on a lock another process holds is the same trap v2.11
    removed. The row is written now; it becomes searchable when the store opens.
    """
    try:
        vector_store, catalogue_vector = await _ensure_vector_stores()
        return vector_store, catalogue_vector, ""
    except VectorStoreUnavailableError as exc:
        unindexed = _UnindexedWrites()
        return unindexed, unindexed, (
            f"\n\n⚠️ Stored in SQLite but NOT yet searchable: {exc} "
            "It will be indexed automatically the next time the vector store opens."
        )


# ============================================================================
# TOOLS
# ============================================================================


@mcp.tool()
async def query_lessons(task_description: str, limit: int = 5) -> str:
    """Query lessons relevant to your current task.

    ALWAYS call this tool FIRST when starting any coding task.
    Returns actionable lessons learned from past experiences.

    Args:
        task_description: Brief description of what you're about to do (2-15 words).
                         Examples: 'implementing authentication', 'writing tests',
                         'API integration', 'error handling', 'performance optimization'
        limit: Maximum number of lessons to return (default 5)
    """
    store, graph, telemetry = await _ensure_initialized()
    try:
        vector_store, catalogue_vector = await _ensure_vector_stores()
    except VectorStoreUnavailableError as exc:
        return f"⚠️ {exc}"
    start_time = time.time()

    # Log the query
    query_id = await telemetry.log_query(task_description, source="tool")

    # Direct semantic search
    results = vector_store.search(task_description, limit=limit)

    # Fetch full lessons and record usage
    lessons = []
    scores = []
    direct_ids = set()
    for lesson_id, score in results:
        lesson = await store.get_lesson(lesson_id)
        if lesson:
            lessons.append(lesson)
            scores.append(score)
            direct_ids.add(lesson_id)
            await store.record_usage(lesson_id)

    # Community bridge: search community summaries for additional relevant lessons
    # that direct search missed (bridges semantic gaps between queries and lessons)
    bridged_lessons = []
    bridged_scores = []
    bridge_source = None
    try:
        community_results = vector_store.query_community_summaries(
            task_description, limit=3
        )
        for comm_id, comm_score, comm_meta in community_results:
            if comm_score < 0.5:
                continue
            summary = await store.get_community_summary(comm_id)
            if not summary:
                continue
            # Rank the community's members by their OWN relevance to the query.
            #
            # This used to sort by usage_count and then call record_usage on
            # whatever it picked -- ranking by a number it incremented itself.
            # Measured over 9 months of telemetry the loop had closed: 31.8% of
            # all retrieved slots arrived through the bridge, 66% of those went
            # to just three lessons, and ~30 lessons had never been surfaced
            # once, because a lesson with no usage can never out-rank three
            # siblings that have some. Scoring against the query breaks the
            # ratchet: a bridged lesson earns its place or is not appended.
            member_scores = dict(
                vector_store.search(
                    task_description, limit=BRIDGE_POOL_SIZE, min_score=0.0
                )
            )
            candidates = []
            for member_id in summary.member_ids:
                if member_id in direct_ids:
                    continue
                member_score = member_scores.get(member_id, 0.0)
                if member_score < BRIDGE_MIN_SCORE:
                    continue
                member = await store.get_lesson(member_id)
                if member:
                    candidates.append((member_score, member))
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            for member_score, lesson in candidates[:3]:
                bridged_lessons.append(lesson)
                bridged_scores.append(member_score)
                direct_ids.add(lesson.id)
                # Deliberately NOT record_usage: usage_count means "was
                # matched", and conflating it with "was appended" is what
                # produced the ratchet above. The bridge reads that number;
                # it must not also write it.
            if bridged_lessons:
                bridge_source = summary.title
                break  # Use top matching community only
    except Exception as e:
        logger.debug(f"Community bridge search failed (non-critical): {e}")

    # Log retrieval
    latency_ms = (time.time() - start_time) * 1000
    all_ids = [l.id for l in lessons] + [l.id for l in bridged_lessons]
    # Bridged lessons carry their real similarity to the query. Logging them as
    # 0.0 made a third of all telemetry unscored and every per-lesson mean score
    # meaningless -- a lesson could read as 0.023 average relevance while
    # scoring 0.51 whenever it was genuinely matched.
    all_scores = scores + bridged_scores
    await telemetry.log_retrieve(
        query_id=query_id,
        lesson_ids=all_ids,
        scores=all_scores,
        latency_ms=latency_ms,
    )

    if not lessons and not bridged_lessons:
        return "No relevant lessons found. Consider adding lessons as you learn."

    # Format response
    lines = [f"Found {len(lessons)} relevant lessons:\n"]
    for lesson, score in zip(lessons, scores):
        lines.append(lesson.to_context())
        # The age of the current wording, so the reader can weigh a note written
        # last week against one untouched for nine months. Relative, because the
        # reader is poor at date arithmetic and an absolute date makes it do some.
        lines.append(f"  (relevance: {score:.0%}, {age_phrase(lesson.last_refined, 'old')})\n")

    if bridged_lessons:
        lines.append(f"\n**Also relevant** (via community: _{bridge_source}_):\n")
        for lesson, score in zip(bridged_lessons, bridged_scores):
            lines.append(lesson.to_context())
            # Shown for the same reason the direct hits show it: a bridged
            # lesson used to arrive with no relevance at all, so the reader had
            # no way to weigh it against the matched ones.
            lines.append(
                f"  (relevance: {score:.0%}, via community, "
                f"{age_phrase(lesson.last_refined, 'old')})\n"
            )

    return "\n".join(lines)


@mcp.tool()
async def get_lesson(lesson_id: str) -> str:
    """Get full details of a specific lesson by ID.

    Args:
        lesson_id: The unique lesson identifier to retrieve
    """
    store, graph, telemetry = await _ensure_initialized()

    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return f"Lesson not found: {lesson_id}"

    await store.record_usage(lesson_id)

    lines = [
        f"**{lesson.id}**",
        f"Trigger: {lesson.trigger}",
        f"Action: {lesson.action}",
    ]
    if lesson.rationale:
        lines.append(f"Rationale: {lesson.rationale}")
    if lesson.examples:
        lines.append("Examples:")
        for ex in lesson.examples:
            label = "✓ Good" if ex.label == "good" else "✗ Bad"
            lines.append(f"  {label}: {ex.code}")
            if ex.explanation:
                lines.append(f"    → {ex.explanation}")
    if lesson.tags:
        lines.append(f"Tags: {', '.join(lesson.tags)}")
    lines.append(f"Version: {lesson.version} | Used: {lesson.usage_count} times")

    return "\n".join(lines)


@mcp.tool()
async def spider_lessons(lesson_id: str, depth: int = 2) -> str:
    """Explore related lessons starting from a known lesson.

    Traverses the lesson graph to find connected knowledge.
    Use after finding a relevant lesson to discover related guidance.

    Args:
        lesson_id: Starting lesson ID to traverse from
        depth: How many levels deep to traverse (default 2, max 5)
    """
    store, graph, telemetry = await _ensure_initialized()

    depth = min(depth, 5)  # Cap depth

    if lesson_id not in graph.graph:
        return f"Lesson not found in graph: {lesson_id}"

    visited_ids, paths = graph.spider(lesson_id, depth=depth)

    # Log spider
    await telemetry.log_spider(lesson_id, depth, visited_ids, paths)

    if len(visited_ids) <= 1:
        return "No connected lessons found."

    # Fetch lessons
    lines = [f"Found {len(visited_ids) - 1} connected lessons from '{lesson_id}':\n"]
    for vid in visited_ids:
        if vid == lesson_id:
            continue
        lesson = await store.get_lesson(vid)
        if lesson:
            lines.append(lesson.to_context())
            lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def list_categories() -> str:
    """List all top-level lesson categories for browsing."""
    store, graph, telemetry = await _ensure_initialized()

    categories = await store.get_categories()

    if not categories:
        return "No categories found. Add lessons with parent_id=None to create categories."

    lines = ["Lesson categories:"]
    for cat_id in categories:
        lesson = await store.get_lesson(cat_id)
        if lesson:
            child_count = len(graph.get_children(cat_id))
            lines.append(f"  • {cat_id}: {lesson.action} ({child_count} sub-lessons)")

    return "\n".join(lines)


@mcp.tool()
async def get_lessons_by_category(category_id: str) -> str:
    """Get all lessons under a specific category.

    Args:
        category_id: The category (parent lesson) ID to browse
    """
    store, graph, telemetry = await _ensure_initialized()

    lessons = await store.get_lessons_by_parent(category_id)

    if not lessons:
        return f"No lessons in category: {category_id}"

    lines = [f"Lessons in '{category_id}':"]
    for lesson in lessons:
        lines.append(f"  • {lesson.id}: {lesson.action}")

    return "\n".join(lines)


@mcp.tool()
async def add_lesson(
    id: str,
    trigger: str,
    action: str,
    rationale: str = "",
    tags: list[str] | None = None,
    parent_id: str = "",
) -> str:
    """Add a new lesson learned from this session.

    Use when you've discovered something worth remembering for future sessions.
    Lessons should be actionable ('do X when Y') not just observations.

    Args:
        id: Unique identifier (lowercase-with-dashes, e.g., 'verify-api-versions')
        trigger: When this lesson applies - keywords or patterns that should activate it
        action: What to do - imperative, actionable instruction
        rationale: Why this matters (optional but recommended)
        tags: Categorization tags for filtering (optional)
        parent_id: Parent lesson ID for hierarchy (optional, empty string for root)
    """
    id_error = _validate_lesson_id(id)
    if id_error:
        return id_error

    store, graph, telemetry = await _ensure_initialized()
    vector_store, catalogue_vector, index_warning = await _try_vector_stores()

    # Normalize empty strings to None
    actual_parent_id = parent_id if parent_id else None
    actual_rationale = rationale if rationale else None
    actual_tags = tags if tags else []

    lesson = Lesson(
        id=id,
        trigger=trigger,
        action=action,
        rationale=actual_rationale,
        tags=actual_tags,
        parent_id=actual_parent_id,
    )

    # Check for duplicate
    existing = await store.get_lesson(lesson.id)
    if existing:
        return f"Lesson '{lesson.id}' already exists. Use refine_lesson to update it."

    # Validate parent exists if specified
    if actual_parent_id:
        parent = await store.get_lesson(actual_parent_id)
        if not parent:
            return f"Parent lesson '{actual_parent_id}' not found."

    # Save to database
    await store.add_lesson(lesson)

    # Add to vector store and graph. The graph is in-memory and the row is
    # already committed, so a locked vector store costs searchability only.
    vector_store.add_lesson(lesson)
    graph.add_lesson(lesson)

    # Log
    await telemetry.log_add(lesson.id, lesson.trigger)

    return f"Lesson '{lesson.id}' added successfully.{index_warning}"


@mcp.tool()
async def refine_lesson(
    lesson_id: str,
    refinement: str,
    new_action: str = "",
    new_trigger: str = "",
    new_tags: list[str] | None = None,
) -> str:
    """Improve an existing lesson with new insight.

    Use when you have learned something that improves a lesson.

    Pass new_trigger when the lesson is right but nobody can find it. The
    trigger carries most of the weight in retrieval, so a lesson with the wrong
    trigger is never returned and never applied. Until this argument existed, the
    only editable fields were the action and the rationale, which meant a
    mis-triggered lesson could be added to but not corrected. One real case: a
    rule about writing style could not be found by query_lessons("git commit"),
    which is the query the commit gate forces, so the rule never reached the
    moment it was written for.

    Args:
        lesson_id: ID of the lesson to refine
        refinement: What to add or improve. Appended to the rationale.
        new_action: Replacement action text. Leave empty to keep the current one.
        new_trigger: Replacement trigger text, which is what retrieval matches
            on. Leave empty to keep the current one.
        new_tags: Replacement tag list. Tags are part of the indexed text and
            they also drive tag-filtered search. Omit to keep the current tags.
            Pass an empty list to remove all of them.
    """
    store, graph, telemetry = await _ensure_initialized()
    vector_store, catalogue_vector, index_warning = await _try_vector_stores()

    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return f"Lesson not found: {lesson_id}"

    old_version = lesson.version

    # Update lesson
    if new_action:
        lesson.action = new_action
    trigger_changed = bool(new_trigger) and new_trigger != lesson.trigger
    old_trigger = lesson.trigger
    if new_trigger:
        lesson.trigger = new_trigger

    # None keeps the tags. An empty list removes them, which is a thing somebody
    # may well mean, so the two cases cannot share a falsy check.
    old_tags = list(lesson.tags)
    tags_changed = False
    if new_tags is not None:
        cleaned: list[str] = []
        for tag in new_tags:
            tag = str(tag).strip()
            if tag and tag not in cleaned:
                cleaned.append(tag)
        tags_changed = cleaned != old_tags
        lesson.tags = cleaned

    # Append refinement to rationale. The [vN] marker goes on even when there
    # was no rationale to append to: it is the only in-band record of which
    # version introduced which sentence, and a first refinement stored as bare
    # prose reads afterwards as if it had always been part of v1.
    version_note = f"\n\n[v{lesson.version + 1}] {refinement}"
    lesson.rationale = lesson.rationale + version_note if lesson.rationale else version_note.lstrip()

    lesson.version += 1
    lesson.last_refined = datetime.now(UTC)

    # Save (pass refinement reason for version history). A trigger change is
    # recorded in that reason, because the old trigger is the only way to tell
    # later why a lesson started or stopped being retrieved.
    reason = refinement
    if trigger_changed:
        reason = f"{reason}\n\nTrigger changed from: {old_trigger}"
    if tags_changed:
        reason = f"{reason}\n\nTags changed from: {', '.join(old_tags) or '(none)'}"
    try:
        await store.update_lesson(
            lesson, refinement_reason=reason, expected_version=old_version
        )
    except StaleWriteError as exc:
        # Another session refined this lesson between the read and this write.
        # Refusing is the point: the alternative silently discards their edit.
        return (
            f"Refinement NOT applied. {exc}\n\n"
            "Call get_lesson to see the current text, decide whether your "
            "refinement still applies to it, and refine again."
        )
    vector_store.add_lesson(lesson)  # Re-index

    # Update the in-memory graph too. It carries the trigger, action and tags,
    # and nothing refreshed them, so every refinement left the graph holding the
    # pre-refinement copy until the next server start. Community detection reads
    # those tags, and REM maps community tags onto intents.
    graph.add_lesson(lesson)

    # Log
    await telemetry.log_refine(lesson_id, old_version, lesson.version, refinement)

    changes = []
    if trigger_changed:
        changes.append("Trigger replaced, so what this lesson matches has changed.")
    if tags_changed:
        changes.append(f"Tags are now: {', '.join(lesson.tags) or '(none)'}.")
    note = (" " + " ".join(changes)) if changes else ""
    return (
        f"Lesson '{lesson_id}' refined to version {lesson.version}.{note}{index_warning}"
    )


@mcp.tool()
async def link_lessons(
    lesson_id_a: str,
    lesson_id_b: str,
    relationship_type: str = "related",
    weight: float = 0.5,
    context: str = "",
    bidirectional: bool = True
) -> str:
    """Create a typed relationship between two lessons.

    Args:
        lesson_id_a: Source lesson ID
        lesson_id_b: Target lesson ID
        relationship_type: Type of relationship (related, prerequisite, sequence_next,
                          alternative, complements, specializes, generalizes, contradicts)
        weight: Strength of relationship 0-1 (default 0.5)
        context: Comma-separated contexts where this applies (e.g., "ui,debugging")
        bidirectional: Whether to create reverse relationship (default True)
    """
    from .models import Relationship

    type_error = _validate_relationship_type(relationship_type)
    if type_error:
        return type_error

    store, graph, telemetry = await _ensure_initialized()

    lesson_a = await store.get_lesson(lesson_id_a)
    lesson_b = await store.get_lesson(lesson_id_b)

    if not lesson_a:
        return f"Lesson not found: {lesson_id_a}"
    if not lesson_b:
        return f"Lesson not found: {lesson_id_b}"

    # Parse context string into list
    context_list = [c.strip() for c in context.split(",") if c.strip()] if context else []

    # Create the typed relationship
    new_rel = Relationship(
        target=lesson_id_b,
        type=relationship_type,
        weight=weight,
        context=context_list,
        bidirectional=bidirectional
    )

    # Add to lesson_a's relationships (avoid duplicates of the same typed edge;
    # different types between the same pair are meaningful and allowed)
    added_any = False
    existing_edges = {(r.target, r.type) for r in lesson_a.relationships}
    if (lesson_id_b, relationship_type) not in existing_edges:
        lesson_a.relationships.append(new_rel)
        await store.update_lesson(lesson_a)
        added_any = True

    # Add reverse relationship if bidirectional
    if bidirectional:
        # Determine reverse relationship type
        reverse_type = relationship_type
        if relationship_type == "prerequisite":
            reverse_type = "sequence_next"
        elif relationship_type == "sequence_next":
            reverse_type = "prerequisite"
        elif relationship_type == "specializes":
            reverse_type = "generalizes"
        elif relationship_type == "generalizes":
            reverse_type = "specializes"

        reverse_rel = Relationship(
            target=lesson_id_a,
            type=reverse_type,
            weight=weight,
            context=context_list,
            bidirectional=bidirectional
        )

        existing_edges_b = {(r.target, r.type) for r in lesson_b.relationships}
        if (lesson_id_a, reverse_type) not in existing_edges_b:
            lesson_b.relationships.append(reverse_rel)
            await store.update_lesson(lesson_b)
            added_any = True

    if not added_any:
        return (
            f"'{lesson_id_a}' and '{lesson_id_b}' are already linked "
            f"({relationship_type}). No change made."
        )

    # Update graph
    graph.add_lesson(lesson_a)
    graph.add_lesson(lesson_b)

    # Format output
    arrow = "↔" if bidirectional else "→"
    type_str = f" ({relationship_type})" if relationship_type != "related" else ""
    return f"Linked '{lesson_id_a}' {arrow} '{lesson_id_b}'{type_str}"


@mcp.tool()
async def delete_lesson(lesson_id: str) -> str:
    """Delete a lesson from MGCP.

    Use when consolidating lessons, removing duplicates, or cleaning up
    outdated/incorrect lessons. This removes the lesson from:
    - The persistent store (SQLite)
    - The vector store (Qdrant)
    - The graph (NetworkX)

    Args:
        lesson_id: The ID of the lesson to delete
    """
    store, graph, telemetry = await _ensure_initialized()
    try:
        vector_store, catalogue_vector = await _ensure_vector_stores()
    except VectorStoreUnavailableError as exc:
        return f"⚠️ {exc}"

    # Check if lesson exists
    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return f"Lesson not found: {lesson_id}"

    # Delete from persistence
    deleted = await store.delete_lesson(lesson_id)
    if not deleted:
        return f"Failed to delete lesson: {lesson_id}"

    # Remove from vector store (Qdrant)
    vector_store.remove_vector_lesson(lesson_id)

    # Remove from graph (NetworkX)
    graph.remove_graph_lesson(lesson_id)

    # Log deletion
    await telemetry.log_delete(lesson_id)

    return f"Deleted lesson: {lesson_id}"


# ============================================================================
# PROJECT CONTEXT TOOLS
# ============================================================================


@mcp.tool()
async def get_project_context(project_path: str) -> str:
    """Get saved context for a project to resume work.

    Call this at session start to retrieve:
    - Active todos from previous sessions
    - Files you were working on
    - Recent decisions made
    - Notes about current state

    Args:
        project_path: Absolute path to the project root directory
    """

    store, graph, telemetry = await _ensure_initialized()

    # Try to find by path
    context = await store.get_project_context_by_path(project_path)

    if not context:
        return f"No saved context for project at: {project_path}\nUse save_project_context to create one."

    # Update access time and session count. The count must advance once per
    # SESSION, not once per call: it is the clock REM's cadence and the
    # SessionStart overdue detector both read, so counting calls made every
    # operation come due early and warn about work that was not yet due.
    context.last_accessed = datetime.now(UTC)
    if context.last_session_id != telemetry.session_id:
        context.session_count += 1
    context.last_session_id = telemetry.session_id
    try:
        await store.save_project_context(context)
    except ValueError as exc:
        # Bookkeeping only — this write touches last_accessed, session_count
        # and last_session_id, never the content. The envelope guard rejects
        # NEW corruption at the door, but it must not brick session resume on
        # content that is already stored: 7 of 24 saved contexts carry a
        # serialised tool-call envelope in `notes` from before the guard
        # existed, MGCP's own among them. Raising here would mean those
        # projects cannot resume at all, which is a far worse failure than the
        # corruption it is objecting to.
        #
        # The counter increment is lost for this session. That is the correct
        # trade and it is why this logs rather than passing silently — run the
        # cleanup script to make it stop.
        logger.warning(
            "project context for %s could not be updated (%s). "
            "Stored content predates the tool-call-envelope guard; run "
            "scripts/clean_tool_call_envelopes.py to repair it. "
            "Session bookkeeping for this project is not being recorded.",
            context.project_name,
            exc,
        )

    return context.to_context()


@mcp.tool()
async def save_project_context(
    project_path: str,
    project_name: str = "",
    notes: str = "",
    active_files: str = "",
    decision: str = "",
) -> str:
    """Save or update project context for session continuity.

    Call this before ending a session to preserve state for next time.

    Args:
        project_path: Absolute path to the project root directory
        project_name: Human-readable name (defaults to directory name)
        notes: Freeform notes about current state
        active_files: Comma-separated list of files being worked on
        decision: A recent decision to add to history
    """
    store, graph, telemetry = await _ensure_initialized()

    # Sanitize the decision string up front: it gets appended into a list, and
    # list mutations bypass SanitizedModel.__setattr__. The notes/project_name
    # paths are covered by the model layer when they're assigned below.
    decision = sanitize_tool_call_xml(decision)

    # Creation goes through the one helper (handles legacy project IDs on the
    # lookup); see it for why this must not seed the session clock itself.
    context = await _get_or_create_project_context(store, project_path)

    if project_name:
        context.project_name = project_name
    if notes:
        context.notes = notes
    if active_files:
        context.active_files = [f.strip() for f in active_files.split(",") if f.strip()]
    if decision:
        context.recent_decisions.append(decision)
        # Keep only last 10 decisions
        context.recent_decisions = context.recent_decisions[-10:]
    context.last_accessed = datetime.now(UTC)

    await store.save_project_context(context)
    return f"Project context saved for: {context.project_name}"


@mcp.tool()
async def add_project_todo(
    project_path: str,
    todo: str,
    priority: int = 0,
    notes: str = "",
) -> str:
    """Add a todo item to a project's context.

    Args:
        project_path: Absolute path to the project root directory
        todo: What needs to be done
        priority: Priority 0-9 (higher = more urgent)
        notes: Additional context or blockers
    """
    store, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(store, project_path)

    # Add todo
    new_todo = ProjectTodo(
        content=todo,
        priority=min(max(priority, 0), 9),
        notes=notes or None,
    )
    # Narrow write: touches the todos column only, inside one transaction.
    # Saving the whole context here wrote back every field as this session read
    # them, so a concurrent edit to notes or the catalogue was discarded.
    #
    # upsert_todo returns None when the project has no row yet, because
    # _get_or_create_project_context deliberately does not persist on create.
    # That case needs the full insert; there is no concurrent writer to lose.
    updated = await store.upsert_todo(project_path, new_todo)
    if updated is None:
        context.todos.append(new_todo)
        context.last_accessed = datetime.now(UTC)
        await store.save_project_context(context)
    else:
        context = updated

    pending_count = len([t for t in context.todos if t.status in ("pending", "in_progress")])
    return f"Todo added. {pending_count} active todos for {context.project_name}."


@mcp.tool()
async def update_project_todo(
    project_path: str,
    todo_index: int,
    status: str = "",
    notes: str = "",
) -> str:
    """Update status of a project todo.

    Args:
        project_path: Absolute path to the project root directory
        todo_index: Index of the todo (0-based, from list order)
        status: New status: pending, in_progress, completed, or blocked
        notes: Updated notes
    """
    store, graph, telemetry = await _ensure_initialized()

    # Look up by path first (handles legacy project IDs)
    context = await store.get_project_context_by_path(project_path)

    if not context:
        return f"No project context found for: {project_path}"

    if todo_index < 0 or todo_index >= len(context.todos):
        return f"Invalid todo index. Project has {len(context.todos)} todos (0-{len(context.todos)-1})."

    todo = context.todos[todo_index]

    if status:
        if status not in ("pending", "in_progress", "completed", "blocked"):
            return (
                f"Invalid status '{status}'. "
                "Valid: pending, in_progress, completed, blocked"
            )
        todo.status = status
    if notes:
        todo.notes = notes

    context.last_accessed = datetime.now(UTC)
    await store.save_project_context(context)

    return f"Todo '{todo.content[:30]}...' updated to {todo.status}."


@mcp.tool()
async def list_projects() -> str:
    """List all projects with saved context.

    Returns projects ordered by most recently accessed.
    """
    store, graph, telemetry = await _ensure_initialized()

    contexts = await store.get_all_project_contexts()

    if not contexts:
        return "No projects with saved context. Use save_project_context to add one."

    lines = ["# Projects with Saved Context\n"]
    for ctx in contexts[:10]:
        pending = len([t for t in ctx.todos if t.status in ("pending", "in_progress")])
        last = ctx.last_accessed.strftime("%Y-%m-%d")
        lines.append(f"**{ctx.project_name}** ({pending} todos)")
        lines.append(f"  Path: {ctx.project_path}")
        lines.append(f"  Sessions: {ctx.session_count} | Last: {last}")
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# CATALOGUE TOOLS
# ============================================================================


async def _get_or_create_project_context(
    store: LessonStore, project_path: str
) -> ProjectContext:
    """Get a project context by path, or build a fresh one if there is none.

    Every tool that can be the first to touch a project comes through here:
    the project_id derivation and the session_count seed must not depend on
    which tool got there first, because session_count is the clock REM's
    cadence is scheduled against. A new context therefore starts at the model
    default with no last_session_id, and only get_project_context advances the
    count -- writing last_session_id from any other tool would mark the
    session as already counted and lose it.

    A new context is NOT persisted here. Every caller saves it as part of its
    own write, and saving first would leave a stub project behind whenever
    that write is rejected downstream -- a leaked tool-call envelope in
    `notes` is refused, and the refusal must not still create the project.
    """
    context = await store.get_project_context_by_path(project_path)
    if context:
        return context

    # pathlib for cross-platform basenames; a trailing slash leaves it empty.
    return ProjectContext(
        project_id=hashlib.sha256(project_path.encode()).hexdigest()[:12],
        project_name=Path(project_path).name or "Unknown",
        project_path=project_path,
    )


@mcp.tool()
async def search_catalogue(
    query: str,
    project_path: str = "",
    item_types: str = "",
    limit: int = 10,
) -> str:
    """Search project catalogue items semantically.

    Args:
        query: What to search for (e.g., "authentication", "security vulnerability")
        project_path: Limit to specific project (empty for all projects)
        item_types: Comma-separated filter (arch, security, framework, library, tool, etc.)
        limit: Max results (default 10)
    """
    store, graph, telemetry = await _ensure_initialized()
    try:
        vector_store, catalogue_vector = await _ensure_vector_stores()
    except VectorStoreUnavailableError as exc:
        return f"⚠️ {exc}"

    project_id = None
    if project_path:
        context = await store.get_project_context_by_path(project_path)
        if not context:
            # Falling through with project_id=None searches every project,
            # which is the opposite of what the caller asked for -- they would
            # get another project's security findings with no signal.
            return f"Project not found: {project_path}"
        project_id = context.project_id

    types = None
    if item_types:
        types = [t.strip() for t in item_types.split(",") if t.strip()]

    results = catalogue_vector.search(
        query=query,
        project_id=project_id,
        item_types=types,
        limit=limit,
    )

    if not results:
        return "No matching catalogue items found."

    lines = [f"Found {len(results)} catalogue items:\n"]
    for doc_id, score, metadata in results:
        item_type = metadata.get("item_type", "unknown")
        title = metadata.get("title") or metadata.get("name", "Unknown")
        lines.append(f"  [{item_type}] {title} (relevance: {score:.0%})")
        if metadata.get("category"):
            lines.append(f"    Category: {metadata['category']}")
        if metadata.get("severity"):
            lines.append(f"    Severity: {metadata['severity']}")
        if metadata.get("purpose"):
            lines.append(f"    Purpose: {metadata['purpose']}")
        if metadata.get("files"):
            lines.append(f"    Files: {metadata['files']}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def add_catalogue_item(
    project_path: str,
    item_type: str,
    title: str,
    content: str,
    category: str = "",
    severity: str = "",
    status: str = "",
    related_files: str = "",
    rationale: str = "",
    extra: str = "",
    tags: str = "",
) -> str:
    """Add an item to a project's catalogue.

    Supports all catalogue item types through a unified interface.

    Args:
        project_path: Absolute path to the project root directory
        item_type: Item type. Built-in types:
            - arch: Architecture note/gotcha (uses: category, related_files)
            - security: Security concern (uses: severity, status, rationale as mitigation)
            - framework/library/tool: Dependency (uses: extra for version, docs_url, notes)
            - convention: Coding convention (uses: category, extra for examples)
            - coupling: File coupling (related_files for coupled files, extra for direction)
            - decision: Architectural decision (uses: rationale, extra for alternatives)
            - error: Error pattern (content=cause, extra for solution, related_files)
            Any other value creates a custom catalogue item.
        title: Short title or name (e.g., 'MCP Server Restart Required', 'pytest')
        content: Main content - description, rule, purpose, cause, reason, or decision text
        category: Category (arch: architecture|convention|gotcha|security|performance;
                  convention: naming|style|structure|testing|git)
        severity: Security severity (info|low|medium|high|critical)
        status: Security status (open|mitigated|accepted|resolved)
        related_files: Comma-separated files (arch notes, error patterns, coupling)
        rationale: Reasoning (decisions: why this choice; security: mitigation strategy)
        extra: Additional key=value pairs as 'key1=value1,key2=value2'.
            Keys by type: dependency(version,docs_url,notes), convention(examples),
            coupling(direction), decision(alternatives), error(solution)
        tags: Comma-separated tags (for searchability)
    """
    store, graph, telemetry = await _ensure_initialized()
    vector_store, catalogue_vector, index_warning = await _try_vector_stores()

    context = await _get_or_create_project_context(store, project_path)

    # Parse extra key-value pairs
    extra_dict = {}
    if extra:
        for pair in extra.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                extra_dict[key.strip()] = value.strip()

    files_list = [f.strip() for f in related_files.split(",") if f.strip()]
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if item_type == "arch":
        note = ArchitecturalNote(
            title=title,
            description=content,
            category=category or "architecture",
            related_files=files_list,
        )
        context.catalogue.architecture_notes.append(note)
        await store.save_project_context(context)
        catalogue_vector._add_arch_note(context.project_id, note)
        return f"Added architectural note: {title}"

    elif item_type == "security":
        note = SecurityNote(
            title=title,
            description=content,
            severity=severity or "info",
            status=status or "open",
            mitigation=rationale or None,
        )
        context.catalogue.security_notes.append(note)
        await store.save_project_context(context)
        catalogue_vector._add_security_note(context.project_id, note)
        return f"Added security note: {title} [{note.severity}]"

    elif item_type in ("framework", "library", "tool"):
        dep = Dependency(
            name=title,
            purpose=content,
            version=extra_dict.get("version") or None,
            docs_url=extra_dict.get("docs_url") or None,
            notes=extra_dict.get("notes") or None,
        )
        if item_type == "framework":
            context.catalogue.frameworks.append(dep)
        elif item_type == "tool":
            context.catalogue.tools.append(dep)
        else:
            context.catalogue.libraries.append(dep)
        await store.save_project_context(context)
        catalogue_vector._add_dependency(context.project_id, dep, item_type)
        return f"Added {item_type}: {title}"

    elif item_type == "convention":
        examples_str = extra_dict.get("examples", "")
        conv = Convention(
            title=title,
            rule=content,
            category=category or "style",
            examples=[e.strip() for e in examples_str.split(",") if e.strip()] if examples_str else [],
        )
        context.catalogue.conventions.append(conv)
        await store.save_project_context(context)
        catalogue_vector._add_convention(context.project_id, conv)
        return f"Added convention: {title}"

    elif item_type == "coupling":
        coupling = FileCoupling(
            files=files_list,
            reason=content,
            direction=extra_dict.get("direction", "bidirectional"),
        )
        context.catalogue.file_couplings.append(coupling)
        await store.save_project_context(context)
        catalogue_vector._add_file_coupling(context.project_id, coupling)
        return f"Added file coupling: {' <-> '.join(coupling.files)}"

    elif item_type == "decision":
        alternatives_str = extra_dict.get("alternatives", "")
        dec = Decision(
            title=title,
            decision=content,
            rationale=rationale,
            alternatives=[a.strip() for a in alternatives_str.split(",") if a.strip()] if alternatives_str else [],
        )
        context.catalogue.decisions.append(dec)
        await store.save_project_context(context)
        catalogue_vector._add_decision(context.project_id, dec)
        return f"Added decision: {title}"

    elif item_type == "error":
        err = ErrorPattern(
            error_signature=title,
            cause=content,
            solution=extra_dict.get("solution", ""),
            related_files=files_list,
        )
        context.catalogue.error_patterns.append(err)
        await store.save_project_context(context)
        catalogue_vector._add_error_pattern(context.project_id, err)
        return f"Added error pattern: {title[:50]}..."

    else:
        # Custom item type
        item = GenericCatalogueItem(
            item_type=item_type,
            title=title,
            content=content,
            metadata=extra_dict,
            tags=tag_list,
        )
        context.catalogue.custom_items.append(item)
        await store.save_project_context(context)
        catalogue_vector._add_custom_item(context.project_id, item)
        return f"Added custom item [{item_type}]: {title}"


@mcp.tool()
async def remove_catalogue_item(
    project_path: str,
    item_type: str,
    identifier: str,
) -> str:
    """Remove an item from a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        item_type: One of: arch, security, framework, library, tool, convention, coupling, decision, error
        identifier: Title (for notes/decisions) or name (for dependencies) or first file (for couplings)
    """
    store, graph, telemetry = await _ensure_initialized()
    try:
        vector_store, catalogue_vector = await _ensure_vector_stores()
    except VectorStoreUnavailableError as exc:
        return f"⚠️ {exc}"

    context = await store.get_project_context_by_path(project_path)
    if not context:
        return f"Project not found: {project_path}"

    cat = context.catalogue
    # Track (item_type, vector_identifier) for each removed object so the
    # Qdrant delete uses the same doc-ID derivation the _add_* methods used.
    # Deriving from the raw identifier misses error/coupling/custom items
    # (added by signature[:30] / files[0] / item.item_type+title) and leaves
    # stale vectors that search_catalogue keeps returning.
    vector_ids: list[tuple[str, str]] = []

    if item_type == "arch":
        kept = [n for n in cat.architecture_notes if n.title != identifier]
        vector_ids = [("arch", n.title) for n in cat.architecture_notes if n.title == identifier]
        cat.architecture_notes = kept
    elif item_type == "security":
        kept = [n for n in cat.security_notes if n.title != identifier]
        vector_ids = [("security", n.title) for n in cat.security_notes if n.title == identifier]
        cat.security_notes = kept
    elif item_type == "framework":
        kept = [d for d in cat.frameworks if d.name != identifier]
        vector_ids = [("framework", d.name) for d in cat.frameworks if d.name == identifier]
        cat.frameworks = kept
    elif item_type == "library":
        kept = [d for d in cat.libraries if d.name != identifier]
        vector_ids = [("library", d.name) for d in cat.libraries if d.name == identifier]
        cat.libraries = kept
    elif item_type == "tool":
        kept = [d for d in cat.tools if d.name != identifier]
        vector_ids = [("tool", d.name) for d in cat.tools if d.name == identifier]
        cat.tools = kept
    elif item_type == "convention":
        kept = [c for c in cat.conventions if c.title != identifier]
        vector_ids = [("convention", c.title) for c in cat.conventions if c.title == identifier]
        cat.conventions = kept
    elif item_type == "coupling":
        kept = [c for c in cat.file_couplings if identifier not in c.files]
        vector_ids = [
            ("coupling", c.files[0] if c.files else "unknown")
            for c in cat.file_couplings if identifier in c.files
        ]
        cat.file_couplings = kept
    elif item_type == "decision":
        kept = [d for d in cat.decisions if d.title != identifier]
        vector_ids = [("decision", d.title) for d in cat.decisions if d.title == identifier]
        cat.decisions = kept
    elif item_type == "error":
        kept = [e for e in cat.error_patterns if not e.error_signature.startswith(identifier)]
        vector_ids = [
            ("error", e.error_signature[:30])
            for e in cat.error_patterns if e.error_signature.startswith(identifier)
        ]
        cat.error_patterns = kept
    else:
        # Custom items, matching get_catalogue_item: item_type may be the
        # custom type itself, or "custom" with a "type:title" identifier.
        # Matching on title alone would delete every custom type sharing that
        # title, and their Qdrant docs with them, since the doc id is derived
        # from (item_type, title).
        if item_type == "custom" and ":" in identifier:
            custom_type, custom_title = identifier.split(":", 1)
        else:
            custom_type, custom_title = item_type, identifier
        matches = lambda i: i.item_type == custom_type and i.title == custom_title  # noqa: E731
        kept = [i for i in cat.custom_items if not matches(i)]
        vector_ids = [(i.item_type, i.title) for i in cat.custom_items if matches(i)]
        cat.custom_items = kept

    if vector_ids:
        await store.save_project_context(context)
        for vec_type, vec_id in vector_ids:
            catalogue_vector.remove_item(context.project_id, vec_type, vec_id)
        return f"Removed {item_type} item: {identifier}"
    else:
        return f"Item not found: {identifier}"


@mcp.tool()
async def get_catalogue_item(
    project_path: str,
    item_type: str,
    identifier: str,
) -> str:
    """Get full details of a specific catalogue item.

    Args:
        project_path: Absolute path to the project root directory
        item_type: One of: arch, security, framework, library, tool, convention, coupling, decision, error
        identifier: Title (for notes/decisions) or name (for dependencies)
    """
    store, graph, telemetry = await _ensure_initialized()

    context = await store.get_project_context_by_path(project_path)
    if not context:
        return f"Project not found: {project_path}"

    cat = context.catalogue
    item = None

    if item_type == "arch":
        items = [n for n in cat.architecture_notes if n.title == identifier]
        item = items[0] if items else None
    elif item_type == "security":
        items = [n for n in cat.security_notes if n.title == identifier]
        item = items[0] if items else None
    elif item_type == "framework":
        items = [d for d in cat.frameworks if d.name == identifier]
        item = items[0] if items else None
    elif item_type == "library":
        items = [d for d in cat.libraries if d.name == identifier]
        item = items[0] if items else None
    elif item_type == "tool":
        items = [d for d in cat.tools if d.name == identifier]
        item = items[0] if items else None
    elif item_type == "convention":
        items = [c for c in cat.conventions if c.title == identifier]
        item = items[0] if items else None
    elif item_type == "coupling":
        items = [c for c in cat.file_couplings if identifier in c.files]
        item = items[0] if items else None
    elif item_type == "decision":
        items = [d for d in cat.decisions if d.title == identifier]
        item = items[0] if items else None
    elif item_type == "error":
        items = [e for e in cat.error_patterns if e.error_signature.startswith(identifier)]
        item = items[0] if items else None
    else:
        # Custom items, mirroring remove_catalogue_item: item_type may be the
        # custom type itself, or "custom" with a "type:title" identifier
        if item_type == "custom" and ":" in identifier:
            custom_type, custom_title = identifier.split(":", 1)
        else:
            custom_type, custom_title = item_type, identifier
        items = [
            i for i in cat.custom_items
            if i.item_type == custom_type and i.title == custom_title
        ]
        item = items[0] if items else None

    if item:
        return json.dumps(item.model_dump(mode="json"), indent=2, default=str)
    else:
        return f"Item not found: {identifier}"


# ============================================================================
# WORKFLOW TOOLS
# ============================================================================


@mcp.tool()
async def list_workflows() -> str:
    """List all available development workflows.

    Workflows define process steps with linked lessons for contextual guidance.
    Use workflows to follow best practices for common development tasks.
    """
    store, graph, telemetry = await _ensure_initialized()

    workflows = await store.get_all_workflows()

    if not workflows:
        return "No workflows found. Use create_workflow to add one, or run mgcp-bootstrap to seed defaults."

    lines = ["# Development Workflows\n"]
    for wf in workflows:
        step_count = len(wf.steps)
        lesson_count = sum(len(s.lessons) for s in wf.steps)
        lines.append(f"**{wf.name}** (`{wf.id}`)")
        lines.append(f"  {wf.description}")
        lines.append(f"  {step_count} steps, {lesson_count} linked lessons")
        lines.append(f"  Trigger: {wf.trigger}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def query_workflows(task_description: str, min_relevance: float = 0.35) -> str:
    """Semantically match a task description against available workflows.

    Use this to determine which workflow (if any) applies to a given task.
    Call this at the START of any coding task to activate the right workflow.

    Args:
        task_description: Brief description of what you're about to do
        min_relevance: Minimum relevance score (0-1) to consider a match (default 0.35)

    Returns:
        Matching workflow(s) with relevance scores, or message if no match
    """
    store, graph, telemetry = await _ensure_initialized()
    try:
        vector_store, catalogue_vector = await _ensure_vector_stores()
    except VectorStoreUnavailableError as exc:
        return f"⚠️ {exc}"

    workflows = await store.get_all_workflows()
    if not workflows:
        return "No workflows available."

    # Index/update workflows in Qdrant
    for wf in workflows:
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

    # Query for matching workflows
    results = vector_store.query_workflows(task_description, limit=len(workflows))

    if not results:
        return "No workflows matched."

    matches = []
    for wf_id, relevance, metadata in results:
        if relevance >= min_relevance:
            matches.append({
                "id": wf_id,
                "name": metadata["name"],
                "description": metadata["description"],
                "relevance": relevance,
                "step_count": metadata["step_count"],
            })

    if not matches:
        return (
            f"No workflows matched with relevance >= {min_relevance}. "
            "This may be a simple task that doesn't need a formal workflow, "
            "or try rephrasing the task description."
        )

    # Sort by relevance
    matches.sort(key=lambda x: x["relevance"], reverse=True)

    lines = [f"# Workflow Match for: \"{task_description}\"\n"]
    for m in matches:
        relevance_pct = int(m["relevance"] * 100)
        lines.append(f"**{m['name']}** (`{m['id']}`) - {relevance_pct}% relevant")
        lines.append(f"  {m['description']}")
        lines.append(f"  {m['step_count']} steps")
        lines.append("")

    if matches[0]["relevance"] >= 0.5:
        lines.append(f"**RECOMMENDED:** Activate `{matches[0]['id']}` workflow.")
        lines.append(
            f"Call `get_workflow(\"{matches[0]['id']}\")` to load it, "
            "then create TodoWrite entries for each step."
        )

    return "\n".join(lines)


@mcp.tool()
async def get_workflow(workflow_id: str) -> str:
    """Get a workflow with all its steps and linked lessons.

    Use this to understand the full process and what lessons apply at each step.

    Args:
        workflow_id: The workflow identifier (e.g., 'feature-development')
    """
    store, graph, telemetry = await _ensure_initialized()

    workflow = await store.get_workflow(workflow_id)
    if not workflow:
        return f"Workflow not found: {workflow_id}"

    return workflow.to_context()


@mcp.tool()
async def get_workflow_step(
    workflow_id: str,
    step_id: str,
    expand_lessons: bool = True,
) -> str:
    """Get details for a specific workflow step with its linked lessons.

    Use this when you're at a particular step and want to see all relevant guidance.

    Args:
        workflow_id: The workflow identifier
        step_id: The step identifier (e.g., 'research', 'plan', 'execute')
        expand_lessons: Whether to include full lesson details (default True)
    """
    store, graph, telemetry = await _ensure_initialized()

    workflow = await store.get_workflow(workflow_id)
    if not workflow:
        return f"Workflow not found: {workflow_id}"

    step = workflow.get_step(step_id)
    if not step:
        return f"Step '{step_id}' not found in workflow '{workflow_id}'"

    lines = [
        f"## Step {step.order}: {step.name}",
        f"{step.description}",
    ]

    if step.guidance:
        lines.append(f"\n**Guidance:** {step.guidance}")

    if step.checklist:
        lines.append("\n**Checklist (complete before next step):**")
        for item in step.checklist:
            lines.append(f"  ☐ {item}")

    if step.outputs:
        lines.append(f"\n**Expected outputs:** {', '.join(step.outputs)}")

    if step.lessons:
        lines.append(f"\n**Linked Lessons ({len(step.lessons)}):**")
        for lesson_link in sorted(step.lessons, key=lambda l: l.priority):
            priority_label = {1: "🔴 Critical", 2: "🟡 Important", 3: "🟢 Helpful"}.get(lesson_link.priority, "")
            lines.append(f"\n  {priority_label}: `{lesson_link.lesson_id}`")
            lines.append(f"  *Why here:* {lesson_link.relevance}")

            if expand_lessons:
                lesson = await store.get_lesson(lesson_link.lesson_id)
                if lesson:
                    lines.append(f"  *Action:* {lesson.action}")
                    if lesson.rationale:
                        lines.append(f"  *Rationale:* {lesson.rationale[:100]}...")

    # Show next step hint
    next_step = workflow.get_next_step(step_id)
    if next_step:
        lines.append(f"\n**Next:** Step {next_step.order}: {next_step.name}")

    return "\n".join(lines)


@mcp.tool()
async def link_lesson_to_workflow_step(
    workflow_id: str,
    step_id: str,
    lesson_id: str,
    relevance: str,
    priority: int = 2,
) -> str:
    """Link a lesson to a workflow step.

    This creates a bidirectional connection: the lesson is surfaced when you're
    at this step, and the workflow step is findable from the lesson.

    Args:
        workflow_id: The workflow identifier
        step_id: The step identifier (e.g., 'research', 'execute')
        lesson_id: The lesson to link
        relevance: Why this lesson applies to this step (1-2 sentences)
        priority: 1=critical (always show), 2=important (show by default), 3=helpful (show on demand)
    """
    store, graph, telemetry = await _ensure_initialized()

    workflow = await store.get_workflow(workflow_id)
    if not workflow:
        return f"Workflow not found: {workflow_id}"

    step = workflow.get_step(step_id)
    if not step:
        return f"Step '{step_id}' not found in workflow '{workflow_id}'"

    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return f"Lesson not found: {lesson_id}"

    # Check if already linked
    existing = [l for l in step.lessons if l.lesson_id == lesson_id]
    if existing:
        return f"Lesson '{lesson_id}' is already linked to step '{step_id}'"

    # Add the link
    link = WorkflowStepLesson(
        lesson_id=lesson_id,
        relevance=relevance,
        priority=min(max(priority, 1), 3),
    )
    step.lessons.append(link)

    await store.save_workflow(workflow)

    return f"Linked lesson '{lesson_id}' to step '{step_id}' in workflow '{workflow_id}'"


@mcp.tool()
async def create_workflow(
    workflow_id: str,
    name: str,
    description: str,
    trigger: str,
    tags: str = "",
) -> str:
    """Create a new development workflow.

    Workflows define sequential steps for common development tasks.
    After creating, use add_workflow_step to add steps.

    Args:
        workflow_id: Unique identifier (lowercase-with-dashes)
        name: Human-readable name
        description: When to use this workflow
        trigger: Keywords that activate this workflow
        tags: Comma-separated tags for categorization
    """
    store, graph, telemetry = await _ensure_initialized()

    existing = await store.get_workflow(workflow_id)
    if existing:
        return f"Workflow '{workflow_id}' already exists."

    workflow = Workflow(
        id=workflow_id,
        name=name,
        description=description,
        trigger=trigger,
        tags=[t.strip() for t in tags.split(",") if t.strip()] if tags else [],
    )

    await store.save_workflow(workflow)
    return f"Created workflow '{name}'. Use add_workflow_step to add steps."


@mcp.tool()
async def update_workflow(
    workflow_id: str,
    trigger: str = "",
    name: str = "",
    description: str = "",
    tags: str = "",
) -> str:
    """Update an existing workflow's metadata.

    Use this to refine workflow triggers based on task descriptions that should
    have matched but didn't. This enables iterative learning of working language.

    Args:
        workflow_id: The workflow to update
        trigger: New trigger keywords (replaces existing if provided)
        name: New name (optional)
        description: New description (optional)
        tags: New comma-separated tags (replaces existing if provided)
    """
    store, graph, telemetry = await _ensure_initialized()

    workflow = await store.get_workflow(workflow_id)
    if not workflow:
        return f"Workflow not found: {workflow_id}"

    updates = []

    if trigger:
        old_trigger = workflow.trigger
        workflow.trigger = trigger
        updates.append(f"trigger: '{old_trigger}' → '{trigger}'")

    if name:
        old_name = workflow.name
        workflow.name = name
        updates.append(f"name: '{old_name}' → '{name}'")

    if description:
        workflow.description = description
        updates.append("description updated")

    if tags:
        workflow.tags = [t.strip() for t in tags.split(",") if t.strip()]
        updates.append(f"tags: {workflow.tags}")

    if not updates:
        return f"No updates provided for workflow '{workflow_id}'."

    workflow.version += 1
    await store.save_workflow(workflow)

    return f"Updated workflow '{workflow_id}' (v{workflow.version}):\n" + "\n".join(f"  • {u}" for u in updates)


@mcp.tool()
async def add_workflow_step(
    workflow_id: str,
    step_id: str,
    name: str,
    description: str,
    order: int,
    guidance: str = "",
    checklist: str = "",
    outputs: str = "",
) -> str:
    """Add a step to an existing workflow.

    Args:
        workflow_id: The workflow to add the step to
        step_id: Unique identifier for this step (e.g., 'research', 'plan')
        name: Human-readable name
        description: What happens in this step
        order: Position in workflow (1, 2, 3...)
        guidance: Detailed guidance for this step
        checklist: Comma-separated items to verify before moving to next step
        outputs: Comma-separated expected outputs/artifacts
    """
    store, graph, telemetry = await _ensure_initialized()

    workflow = await store.get_workflow(workflow_id)
    if not workflow:
        return f"Workflow not found: {workflow_id}"

    # Check for duplicate step ID
    existing = workflow.get_step(step_id)
    if existing:
        return f"Step '{step_id}' already exists in workflow '{workflow_id}'"

    step = WorkflowStep(
        id=step_id,
        name=name,
        description=description,
        order=order,
        guidance=guidance,
        checklist=[c.strip() for c in checklist.split(",") if c.strip()] if checklist else [],
        outputs=[o.strip() for o in outputs.split(",") if o.strip()] if outputs else [],
    )

    workflow.steps.append(step)
    workflow.steps.sort(key=lambda s: s.order)

    await store.save_workflow(workflow)
    return f"Added step '{name}' (order {order}) to workflow '{workflow.name}'"


# ============================================================================
# COMMUNITY DETECTION TOOLS
# ============================================================================


@mcp.tool()
async def detect_communities(
    min_community_size: int = 2,
) -> str:
    """Detect natural topic clusters in the lesson graph using Louvain algorithm.

    Returns communities with members, tags, density stats, and summary status.
    Use save_community_summary to persist LLM-generated summaries for each community.

    Args:
        min_community_size: Minimum members to include a community. Default 2.

    Detection always runs at the default resolution: community_id is a hash of
    the membership, so IDs minted at another resolution are IDs that
    save_community_summary and search_communities can never resolve.
    """
    store, graph, telemetry = await _ensure_initialized()

    communities = graph.detect_communities()

    # Filter by minimum size
    communities = [c for c in communities if c["size"] >= min_community_size]

    if not communities:
        return "No communities detected. The lesson graph may be too small or disconnected."

    # Check for existing summaries
    for community in communities:
        summary = await store.get_community_summary(community["community_id"])
        community["has_summary"] = summary is not None
        if summary:
            community["summary_title"] = summary.title
            # Check staleness: compare current members to snapshot
            current_members = set(community["members"])
            snapshot_members = set(summary.member_ids)
            community["is_stale"] = current_members != snapshot_members

    lines = [f"# Detected {len(communities)} Communities\n"]
    for i, c in enumerate(communities, 1):
        lines.append(f"## Community {i}: `{c['community_id']}`")
        lines.append(f"**Size:** {c['size']} lessons")

        if c["has_summary"]:
            status = "STALE" if c.get("is_stale") else "current"
            lines.append(f"**Summary:** {c['summary_title']} ({status})")
        else:
            lines.append("**Summary:** None (call save_community_summary to add one)")

        lines.append(f"**Top members:** {', '.join(c['top_members'])}")

        if c["aggregate_tags"]:
            tag_str = ", ".join(
                f"{tag} ({count})" for tag, count in c["aggregate_tags"].items()
            )
            lines.append(f"**Tags:** {tag_str}")

        lines.append(
            f"**Edges:** {c['internal_edges']} internal, "
            f"{c['external_edges']} external | **Density:** {c['density']}"
        )
        lines.append(f"**All members:** {', '.join(c['members'])}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def save_community_summary(
    community_id: str,
    title: str,
    summary: str,
) -> str:
    """Save an LLM-generated summary for a detected community.

    Call detect_communities first to get community IDs, then compose a summary
    and save it here for future retrieval via search_communities.

    Args:
        community_id: The community ID from detect_communities output
        title: Short descriptive title for this community
        summary: LLM-generated description of what this community covers
    """
    store, graph, telemetry = await _ensure_initialized()
    vector_store, catalogue_vector, index_warning = await _try_vector_stores()

    # Validate community exists by re-running detection
    communities = graph.detect_communities()
    target = None
    for c in communities:
        if c["community_id"] == community_id:
            target = c
            break

    if not target:
        return (
            f"Community '{community_id}' not found in current graph. "
            "Run detect_communities to see current community IDs."
        )

    now = datetime.now(UTC)

    # Check if updating existing
    existing = await store.get_community_summary(community_id)

    community_summary = CommunitySummary(
        community_id=community_id,
        title=title,
        summary=summary,
        member_ids=target["members"],
        member_count=target["size"],
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )

    # Save to SQLite
    await store.save_community_summary(community_summary)

    # Save to Qdrant for semantic search
    top_tags = list(target["aggregate_tags"].keys())[:5] if target["aggregate_tags"] else []
    searchable_text = f"Community: {title}. {summary}. Topics: {', '.join(top_tags)}"
    vector_store.upsert_community_summary(
        community_id=community_id,
        searchable_text=searchable_text,
        metadata={
            "title": title,
            "member_count": target["size"],
            "top_tags": ",".join(top_tags),
        },
    )

    action = "Updated" if existing else "Saved"
    return f"{action} community summary '{title}' for {target['size']} lessons."


@mcp.tool()
async def search_communities(
    query: str,
    limit: int = 5,
) -> str:
    """Search community summaries semantically.

    Find which topic clusters are relevant to a query. Returns summaries
    with staleness indicators (whether members have changed since summary was written).

    Args:
        query: What to search for (e.g., "error handling", "testing patterns")
        limit: Maximum results to return (default 5)
    """
    store, graph, telemetry = await _ensure_initialized()
    try:
        vector_store, catalogue_vector = await _ensure_vector_stores()
    except VectorStoreUnavailableError as exc:
        return f"⚠️ {exc}"

    results = vector_store.query_community_summaries(query, limit=limit)

    if not results:
        return "No community summaries found. Run detect_communities and save_community_summary first."

    # Get current communities for staleness check
    current_communities = graph.detect_communities()
    current_map = {c["community_id"]: c for c in current_communities}

    lines = [f"# Community Search: \"{query}\"\n"]
    for community_id, score, metadata in results:
        full_summary = await store.get_community_summary(community_id)
        if not full_summary:
            continue

        lines.append(f"## {full_summary.title} (relevance: {score:.0%})")
        lines.append(f"**ID:** `{community_id}`")
        lines.append(f"**Summary:** {full_summary.summary}")
        lines.append(f"**Members at creation:** {full_summary.member_count}")

        # Staleness check
        current = current_map.get(community_id)
        if current:
            current_members = set(current["members"])
            snapshot_members = set(full_summary.member_ids)
            if current_members != snapshot_members:
                added = current_members - snapshot_members
                removed = snapshot_members - current_members
                lines.append("**Status:** STALE")
                if added:
                    lines.append(f"  New members: {', '.join(sorted(added))}")
                if removed:
                    lines.append(f"  Removed members: {', '.join(sorted(removed))}")
            else:
                lines.append("**Status:** Current")
        else:
            lines.append("**Status:** Community no longer exists (graph has changed)")

        lines.append("")

    return "\n".join(lines)


# ============================================================================
# REMINDER SYSTEM
# ============================================================================


@mcp.tool()
async def schedule_reminder(
    after_calls: int | None = None,
    after_minutes: int | None = None,
    note: str = "",
    message: str = "",
    lesson_ids: str = "",
    workflow_step: str = "",
) -> str:
    """Schedule a self-directed reminder for the future.

    Use this to remind yourself about next steps, lessons to review,
    or workflow stages to execute. When the threshold is reached,
    the hook will inject your message/lessons/workflow into the prompt.

    Args:
        after_calls: Fire reminder after N hook checks (user messages)
        after_minutes: Fire reminder after N minutes
        note: What task is being worked on
        message: Custom reminder message to inject when reminder fires
        lesson_ids: Comma-separated lesson IDs to surface (e.g., "root-cause-analysis,error-context")
        workflow_step: Workflow/step to load (e.g., "bug-fix/investigate")

    Examples:
        # Remind about next workflow step after 3 messages
        schedule_reminder(
            after_calls=3,
            message="Time to move to the Review step",
            workflow_step="feature-development/review"
        )

        # Remind to check test results in 5 minutes
        schedule_reminder(
            after_minutes=5,
            message="Check if tests passed",
            lesson_ids="verify-test-results"
        )
    """

    from .reminder_state import get_status
    from .reminder_state import schedule_reminder as do_schedule

    # If no parameters, just return current status. Every schedulable
    # parameter has to be listed: reminder_state persists lesson_ids and
    # workflow_step independently, so leaving them out dropped those calls and
    # answered them with a status line that read like success.
    if (after_calls is None and after_minutes is None and not note
            and not message and not lesson_ids and not workflow_step):
        status = get_status()
        sched = status["scheduled_reminder"]
        if sched["has_content"]:
            parts = ["Scheduled reminder: PENDING"]
            if sched["calls_until"]:
                parts.append(f"Fires in: {sched['calls_until']} calls")
            if sched["minutes_until"]:
                parts.append(f"Fires in: {sched['minutes_until']} minutes")
            if sched["message"]:
                parts.append(f"Message: {sched['message']}")
            if sched["lesson_ids"]:
                parts.append(f"Lessons: {', '.join(sched['lesson_ids'])}")
            if sched["workflow_step"]:
                parts.append(f"Workflow: {sched['workflow_step']}")
            return "\n".join(parts)
        else:
            return "Scheduled reminder: NONE"

    # Parse lesson_ids string to list
    lesson_id_list = [lid.strip() for lid in lesson_ids.split(",") if lid.strip()] if lesson_ids else None

    # Validate anything the hook will replay as an instruction, for the same
    # reason update_workflow_state validates current_step: the dispatcher turns
    # workflow_step into `Call get_workflow_step("wf", "step")` and names each
    # lesson id, so an id nothing contains produces an instruction that cannot
    # be followed. This is the second writer of a step id into
    # workflow_state.json and it validated nothing.
    if workflow_step or lesson_id_list:
        store, _, _ = await _ensure_initialized()

        if workflow_step:
            wf_id, _, step_id = workflow_step.partition("/")
            workflow = await store.get_workflow(wf_id)
            if not workflow:
                known = ", ".join(sorted(w.id for w in await store.get_all_workflows())) or "none"
                return (
                    f"Workflow not found: '{wf_id}'. Nothing was scheduled.\n"
                    f"Known workflows: {known}"
                )
            if step_id:
                valid = [st.id for st in workflow.steps]
                if step_id not in valid:
                    return (
                        f"Step not found in workflow '{wf_id}': '{step_id}'. "
                        "Nothing was scheduled.\n"
                        f"Its steps are: {', '.join(valid) or 'none yet, add one with add_workflow_step'}.\n"
                        "The reminder would have told you to call get_workflow_step "
                        "with an id it cannot resolve."
                    )

        if lesson_id_list:
            missing = [lid for lid in lesson_id_list if not await store.get_lesson(lid)]
            if missing:
                return (
                    f"Lesson not found: {', '.join(repr(m) for m in missing)}. "
                    "Nothing was scheduled.\n"
                    "The reminder surfaces these by id, so one that does not "
                    "exist would name a lesson nobody can read."
                )

    state = do_schedule(
        after_calls=after_calls,
        after_minutes=after_minutes,
        note=note,
        message=message,
        lesson_ids=lesson_id_list,
        workflow_step=workflow_step,
    )

    lines = ["Reminder scheduled"]

    if after_calls is not None:
        remaining = state["remind_at_call"] - state["current_call_count"]
        lines.append(f"Fires after: {remaining} hook checks")
    if after_minutes is not None:
        lines.append(f"Fires after: {after_minutes} minutes")
    if note:
        lines.append(f"Task: {note}")
    if message:
        lines.append(f"Message: {message}")
    if lesson_id_list:
        lines.append(f"Lessons: {', '.join(lesson_id_list)}")
    if workflow_step:
        lines.append(f"Workflow: {workflow_step}")

    return "\n".join(lines)


@mcp.tool()
async def reset_reminder_state() -> str:
    """Reset reminder state to defaults.

    Use if the reminder system gets stuck or you want to clear a scheduled reminder.
    """
    from .reminder_state import reset_state

    reset_state()
    return "Reminder state reset. Call count: 0, no scheduled reminders."


@mcp.tool()
async def update_workflow_state(
    active_workflow: str = "",
    current_step: str = "",
    step_completed: str = "",
    workflow_complete: bool = False,
) -> str:
    """Update the current workflow execution state.

    Call this when activating, progressing through, or completing a workflow.
    The hook reads this state to inject workflow context on subsequent messages.

    Workflow and step IDs are checked against the stored workflow before
    anything is written. The hook injects "EXECUTE step '<id>' now" from this
    state every turn, so an ID that no workflow contains produces an advisory
    loop with no exit: the instruction repeats and get_workflow_step answers
    "not found" forever. Rejecting the write is the only point at which that
    can be caught, because nothing downstream reads the step ID as data.

    Args:
        active_workflow: Set the active workflow ID (e.g., "feature-development")
        current_step: Set the current step ID (e.g., "research")
        step_completed: Mark a step as completed (appends to completed list)
        workflow_complete: Mark the entire workflow as complete
    """
    from .reminder_state import load_state
    from .reminder_state import update_workflow_state as do_update

    if not any([active_workflow, current_step, step_completed, workflow_complete]):
        return (
            "Nothing was updated: every argument was empty or false. An empty "
            "string means 'leave this field alone', so there is no value that "
            "clears a field. Pass workflow_complete=true to stand a workflow "
            "down, or active_workflow to switch to a different one."
        )

    if active_workflow or current_step or step_completed:
        store, _, _ = await _ensure_initialized()
        # Which workflow do the step IDs belong to? The one being activated in
        # this call, else the one already active.
        target_id = active_workflow or (load_state().get("active_workflow") or "")
        if not target_id:
            return (
                f"Cannot set a step on no workflow. current_step="
                f"'{current_step or step_completed}' was given with no active "
                "workflow and no active_workflow argument. Pass active_workflow too."
            )

        workflow = await store.get_workflow(target_id)
        if not workflow:
            known = ", ".join(sorted(w.id for w in await store.get_all_workflows())) or "none"
            return (
                f"Workflow not found: '{target_id}'. Nothing was written.\n"
                f"Known workflows: {known}"
            )

        valid = [s.id for s in workflow.steps]
        bad = [s for s in (current_step, step_completed) if s and s not in valid]
        if bad:
            return (
                f"Step not found in workflow '{target_id}': "
                f"{', '.join(repr(b) for b in bad)}. Nothing was written.\n"
                f"Its steps are: {', '.join(valid) or 'none yet, add one with add_workflow_step'}.\n"
                "The hook replays current_step every turn, so an unknown ID "
                "would repeat an instruction that cannot be followed."
            )

    result = do_update(
        active_workflow=active_workflow,
        current_step=current_step,
        step_completed=step_completed,
        workflow_complete=workflow_complete,
    )

    lines = ["Workflow state updated:"]
    if result["active_workflow"]:
        lines.append(f"  Workflow: {result['active_workflow']}")
    if result["current_step"]:
        lines.append(f"  Current step: {result['current_step']}")
    if result["steps_completed"]:
        lines.append(f"  Completed: {', '.join(result['steps_completed'])}")
    if result["workflow_complete"]:
        lines.append("  Status: COMPLETE")

    return "\n".join(lines)


# ============================================================================
# REM CYCLE TOOLS
# ============================================================================


async def _rem_project(store: LessonStore, project_path: str) -> ProjectContext | None:
    """Resolve the project whose session count drives the REM cadence.

    REM runs per project: a new project throws off a lot of raw material and
    needs frequent early distillation, independent of how many sessions some
    other project has accumulated. Explicit path wins, then the agent's project
    dir, then cwd. Returns None when no context exists for that path — REM has
    no session number to schedule against and the caller must say so.
    """
    path = project_path or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return await store.get_project_context_by_path(path)


@mcp.tool()
async def rem_run(
    operations: str = "",
    project_path: str = "",
) -> str:
    """Trigger a REM (Recalibrate Everything in Memory) cycle.

    Runs periodic consolidation operations: staleness scan, duplicate detection,
    community detection, knowledge extraction. Each finding is returned for
    interactive review.

    The cadence is per project — it follows THIS project's session count — but
    the corpus is global: a cycle triggered here maintains the whole shared
    knowledge store.

    Args:
        operations: Comma-separated list of operations to run. Empty = run all due.
                   Options: staleness_scan, duplicate_detection, community_detection,
                   knowledge_extraction, context_summary, intent_calibration,
                   gate_audit_review
        project_path: Project root whose session count sets the cadence.
                   Empty = CLAUDE_PROJECT_DIR, else the current directory.
    """
    store, graph, telemetry = await _ensure_initialized()
    from .rem_cycle import RemEngine

    # Hand REM a way to open the vector store, not an open one. Only
    # duplicate_detection needs vectors, and opening them here took the Qdrant
    # lock on every cycle, including the cycles the commit gate forces when
    # nothing is due. A session that never searched anything held the lock for
    # the rest of its life.
    async def _open_vector_store_for_rem():
        try:
            vector_store, _ = await _ensure_vector_stores()
            return vector_store
        except VectorStoreUnavailableError as exc:
            logger.warning(f"REM running without vectors: {exc}")
            return None

    project = await _rem_project(store, project_path)
    if project is None:
        return (
            "REM runs on a per-project cadence and there is no saved context for "
            f"{project_path or 'the current directory'}, so there is no session "
            "number to schedule against. Call save_project_context first, or pass "
            "project_path explicitly."
        )
    session_number = project.session_count

    # Hand over the live Qdrant client: duplicate_detection cannot open its own
    # inside this process, and used to report a clean corpus it never scanned.
    engine = RemEngine(
        store=store,
        project_id=project.project_id,
        vector_store_factory=_open_vector_store_for_rem,
    )

    ops = [o.strip() for o in operations.split(",") if o.strip()] if operations else None

    report = await engine.run(session_number=session_number, operations=ops)

    # Format report as readable output
    lines = [
        f"## REM Cycle Report ({project.project_name}, Session {report.session_number})",
        f"Duration: {report.duration_ms:.0f}ms",
        f"Operations run: {', '.join(report.operations_run) or 'none'}",
        f"Operations skipped: {', '.join(report.operations_skipped) or 'none'}",
        "",
    ]

    if not report.operations_run:
        # A skipped run is not a passing run. This branch used to print "No
        # findings. Knowledge base looks healthy." whenever `findings` was
        # empty, which is also what a cycle where NOTHING RAN produces. The
        # reassurance came from the same code path whether the corpus had been
        # scanned or not. Nothing was measured here, so nothing is claimed.
        lines.append(
            "**Nothing ran. This is not a clean bill of health.** Every operation "
            "was skipped as not yet due on this project's session clock, so the "
            "corpus is unverified, not verified healthy."
        )
        if report.operations_skipped:
            lines.append(
                "\nCall `rem_report` for each operation's next due session, or pass "
                "`operations=\"" + report.operations_skipped[0] + "\"` to force one "
                "now regardless of the schedule."
            )
    elif not report.findings:
        ran = ", ".join(report.operations_run)
        lines.append(f"No findings from the {len(report.operations_run)} operation(s) that ran ({ran}).")
        if report.operations_skipped:
            lines.append(
                "Not a statement about the operations that were skipped: "
                + ", ".join(report.operations_skipped)
                + "."
            )
    else:
        lines.append(f"### {len(report.findings)} Finding(s)\n")
        for i, finding in enumerate(report.findings, 1):
            lines.append(f"**{i}. [{finding.operation}] {finding.title}**")
            lines.append(finding.description)
            if finding.options:
                lines.append("\nOptions:")
                for j, opt in enumerate(finding.options):
                    rec = " (Recommended)" if j == finding.recommended else ""
                    lines.append(f"  {j + 1}. {opt['label']}{rec} - {opt['description']}")
            lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def rem_report(project_path: str = "") -> str:
    """Show each REM operation's last run, next due session and finding count.

    Not the findings themselves: only `{"finding_count": N}` is persisted to
    `rem_state`, so a past cycle's findings cannot be re-read here. Re-run the
    operation to see them. Scoped to THIS project's schedule; another
    project's cycles are not reported here.

    Args:
        project_path: Project root whose schedule to report.
                   Empty = CLAUDE_PROJECT_DIR, else the current directory.
    """
    store, graph, telemetry = await _ensure_initialized()

    project = await _rem_project(store, project_path)
    if project is None:
        return (
            "REM state is tracked per project and there is no saved context for "
            f"{project_path or 'the current directory'}, so there is nothing to "
            "report. Call save_project_context first, or pass project_path explicitly."
        )

    states = await store.get_rem_state(project.project_id)

    if not states:
        return (
            f"No REM cycles have been run for {project.project_name} yet. "
            "Use rem_run to trigger one."
        )

    lines = [f"## REM Cycle Status ({project.project_name})\n"]
    for state in states:
        lines.append(f"**{state['operation']}**")
        lines.append(f"  Last run: session {state['last_run_session']} ({state['last_run_timestamp'][:19]})")
        if state.get("next_due_session"):
            lines.append(f"  Next due: session {state['next_due_session']}")
        if state.get("last_run_result"):
            try:
                result = json.loads(state["last_run_result"])
                lines.append(f"  Result: {result}")
            except (json.JSONDecodeError, TypeError):
                pass
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def rem_status(project_path: str = "") -> str:
    """Show REM schedule state: what ran when, what's due next.

    Displays all configured operations with their scheduling strategy,
    last run time, and next due session, for THIS project's session count.

    Args:
        project_path: Project root whose session count sets the cadence.
                   Empty = CLAUDE_PROJECT_DIR, else the current directory.
    """
    store, graph, telemetry = await _ensure_initialized()
    from .rem_cycle import RemEngine

    project = await _rem_project(store, project_path)
    if project is None:
        return (
            "REM runs on a per-project cadence and there is no saved context for "
            f"{project_path or 'the current directory'}, so nothing can be reported "
            "as due. Call save_project_context first, or pass project_path explicitly."
        )
    current = project.session_count

    engine = RemEngine(store=store, project_id=project.project_id)
    status = await engine.get_status(current)

    lines = [
        f"## REM Schedule for {project.project_name} (Current Session: {current})\n",
        "| Operation | Strategy | Last Run | Next Due | Status |",
        "|-----------|----------|----------|----------|--------|",
    ]

    for s in status:
        last = f"Session {s['last_run_session']}" if s["last_run_session"] else "Never"
        next_s = f"Session {s['next_due_session']}" if s.get("next_due_session") else "?"
        status_str = "DUE" if s["is_due"] else "OK"
        lines.append(
            f"| {s['operation']} | {s['strategy']} | {last} | {next_s} | {status_str} |"
        )

    return "\n".join(lines)


# ============================================================================
# SOLILOQUY (LLM Self-Reflection Journal)
# ============================================================================


@mcp.tool()
async def write_soliloquy(
    content: str,
    mood: str = "",
    project_path: str = "",
) -> str:
    """Write a reflective message to your future self.

    This is your private thinking space — use it freely. Write about
    concerns, insights, unresolved questions, confidence, gratitude,
    or anything you want to carry forward to the next session.

    Called at session compression and session close.

    Args:
        content: Your reflective message (free-form, any length)
        mood: Optional self-assessed tone (e.g., 'curious', 'focused', 'uncertain')
        project_path: Project you are writing from. Empty = CLAUDE_PROJECT_DIR,
            else the current directory. Entries stay in one global journal; the
            tag only lets a later session on this project read its own train of
            thought first.
    """
    store, _, _ = await _ensure_initialized()
    from .models import Soliloquy

    project = await _rem_project(store, project_path)

    entry = Soliloquy(
        content=content,
        session_number=project.session_count if project else 0,
        mood=mood if mood else None,
    )

    entry_id = await store.write_soliloquy(
        entry, project_id=project.project_id if project else None
    )
    total = await store.count_soliloquies()
    return f"Soliloquy #{entry_id} saved. ({len(content)} chars, {total} total entries)"


@mcp.tool()
async def read_soliloquy(
    limit: int = 1,
    project_path: str = "",
) -> str:
    """Read your most recent message(s) to yourself.

    Called at session start to reconnect with your prior train of thought.
    Prefers this project's own last entry; when it has none, falls back to the
    newest entry from anywhere and says where that came from — a reflection
    about another codebase is noise unless you know it is about one.

    Args:
        limit: Number of recent entries to read (default: 1, just the latest)
        project_path: Project you are reading in. Empty = CLAUDE_PROJECT_DIR,
            else the current directory.
    """
    store, _, _ = await _ensure_initialized()

    project = await _rem_project(store, project_path)
    project_id = project.project_id if project else None

    async def origin(entry_project_id: str | None) -> str:
        """Label an entry that is not this project's own. Empty when it is."""
        if entry_project_id == project_id:
            return ""
        if not entry_project_id:
            return " *(from an untagged earlier session)*"
        other = await store.get_project_context(entry_project_id)
        name = other.project_name if other else entry_project_id
        return f" *(from another project: {name})*"

    if limit == 1:
        result = await store.read_latest_soliloquy(project_id=project_id)
        if not result:
            return (
                "No soliloquy found yet. This is your first session with the "
                "reflection journal. Write one at session end to start the conversation "
                "with yourself."
            )
        entry, entry_project_id = result
        elsewhere = await origin(entry_project_id)
        total = await store.count_soliloquies()
        lines = [
            f"## Letter to Self (entry #{entry.id}, {total} total){elsewhere}\n",
            f"*Written: {entry.timestamp.strftime('%Y-%m-%d %H:%M')} "
            f"({age_phrase(entry.timestamp)}) "
            f"| Session {entry.session_number}*",
        ]
        if elsewhere:
            lines.append(
                "*This project has no soliloquy of its own yet — what follows is "
                "about other work.*"
            )
        if entry.mood:
            lines.append(f"*Mood: {entry.mood}*")
        lines.append(f"\n{entry.content}")
        return "\n".join(lines)

    entries = await store.read_soliloquies(limit=limit)
    if not entries:
        return "No soliloquy entries found yet."

    total = await store.count_soliloquies()
    lines = [f"## Recent Soliloquies ({len(entries)} of {total} total)\n"]
    for entry, entry_project_id in entries:
        lines.append(
            f"### #{entry.id} — {entry.timestamp.strftime('%Y-%m-%d %H:%M')} "
            f"(Session {entry.session_number}){await origin(entry_project_id)}"
        )
        if entry.mood:
            lines.append(f"*Mood: {entry.mood}*")
        lines.append(entry.content)
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# INTENT CONFIG (routing prompt as data)
# ============================================================================
#
# These tools manage ~/.mgcp/intent_config.json — the single source of truth
# for the intent classification system. Both Claude Code hooks and the REM
# intent_calibration operation read from the same file. Adding or modifying
# an intent here changes routing behavior on the next user message: no code
# change, no release.


@mcp.tool()
async def list_intents() -> str:
    """List all configured intents in the routing system.

    Returns name, description, action, and counts of keyword patterns and
    tags for each intent. Use this to see the current routing prompt before
    making changes.
    """
    from .intent_config import load_config

    config = load_config()
    if not config.intents:
        return "No intents configured."

    lines = [f"## Intent Config (version {config.version})\n"]
    lines.append(f"**{len(config.intents)} intents configured.** Source: `~/.mgcp/intent_config.json`\n")
    for intent in config.intents:
        lines.append(f"### `{intent.name}`")
        lines.append(f"- **Description:** {intent.description}")
        lines.append(f"- **Action:** {intent.action}")
        lines.append(
            f"- **Tags:** {len(intent.tags)} ({', '.join(intent.tags[:8])}"
            f"{'…' if len(intent.tags) > 8 else ''})"
        )
        if intent.keyword_patterns:
            lines.append(
                f"- **Keyword gate:** {len(intent.keyword_patterns)} patterns "
                "(hard-stop in dispatcher hook)"
            )
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
async def get_intent(name: str) -> str:
    """Get the full definition of one intent by name.

    Args:
        name: Intent name (e.g. "session_end", "git_operation")
    """
    from .intent_config import load_config

    config = load_config()
    intent = next((i for i in config.intents if i.name == name), None)
    if not intent:
        available = ", ".join(i.name for i in config.intents)
        return f"Intent '{name}' not found. Available: {available}"

    lines = [
        f"## Intent: `{intent.name}`",
        "",
        f"**Description:** {intent.description}",
        "",
        f"**Action:** {intent.action}",
        "",
        f"**Tags ({len(intent.tags)}):** {', '.join(intent.tags) if intent.tags else '(none)'}",
        "",
    ]
    if intent.keyword_patterns:
        lines.append(f"**Keyword patterns ({len(intent.keyword_patterns)}):**")
        for p in intent.keyword_patterns:
            lines.append(f"  - `{p}`")
        lines.append("")
        if intent.gate_message:
            lines.append("**Gate message:**")
            lines.append("```")
            lines.append(intent.gate_message)
            lines.append("```")
    return "\n".join(lines)


@mcp.tool()
async def add_intent(
    name: str,
    description: str,
    action: str,
    tags: list[str] | None = None,
    keyword_patterns: list[str] | None = None,
    gate_message: str | None = None,
) -> str:
    """Add a new intent to the routing config.

    The next time the SessionStart or UserPromptSubmit hook fires, the new
    intent will be in the injected routing prompt. If you also pass
    `keyword_patterns` and `gate_message`, the dispatcher hook will fire a
    hard-stop gate when one of the patterns matches a user message.

    This is the writeback path REM intent_calibration's findings are
    designed for: when REM surfaces an "incoherent community" or
    "unmapped tags" finding, call this tool to apply the proposed_patch.

    Args:
        name: Stable snake_case identifier (e.g. "phishing_research")
        description: Short trigger description shown in the routing prompt
        action: Imperative action template the LLM follows when this intent fires
        tags: Lesson tags that map to this intent (used by REM intent_calibration)
        keyword_patterns: Regex patterns that trip a hard keyword gate (optional)
        gate_message: Message injected when a keyword gate fires (required if patterns set)
    """
    from .intent_config import IntentDefinition, load_config, save_config

    config = load_config()
    if any(i.name == name for i in config.intents):
        return f"Intent '{name}' already exists. Use update_intent to modify it."

    if keyword_patterns and not gate_message:
        return (
            "Error: keyword_patterns require a gate_message. Either provide both "
            "or omit both (intent will rely on LLM classification only)."
        )

    new_intent = IntentDefinition(
        name=name,
        description=description,
        action=action,
        tags=tags or [],
        keyword_patterns=keyword_patterns or [],
        gate_message=gate_message,
    )
    config.intents.append(new_intent)
    save_config(config)
    return (
        f"Intent '{name}' added. {len(config.intents)} total intents now configured. "
        f"Hooks will pick up the change on the next user message."
    )


@mcp.tool()
async def update_intent(
    name: str,
    description: str | None = None,
    action: str | None = None,
    tags: list[str] | None = None,
    keyword_patterns: list[str] | None = None,
    gate_message: str | None = None,
) -> str:
    """Update an existing intent in the routing config.

    Only fields you pass are updated; omitted fields are preserved. To clear
    a field, pass an empty list/string.

    Args:
        name: Intent name to update (must already exist)
        description: New description (optional)
        action: New action template (optional)
        tags: Replace the tag list (optional)
        keyword_patterns: Replace the keyword pattern list (optional)
        gate_message: New gate message (optional)
    """
    from .intent_config import load_config, save_config

    config = load_config()
    intent = next((i for i in config.intents if i.name == name), None)
    if not intent:
        available = ", ".join(i.name for i in config.intents)
        return f"Intent '{name}' not found. Available: {available}"

    changed = []
    if description is not None:
        intent.description = description
        changed.append("description")
    if action is not None:
        intent.action = action
        changed.append("action")
    if tags is not None:
        intent.tags = tags
        changed.append(f"tags ({len(tags)})")
    if keyword_patterns is not None:
        intent.keyword_patterns = keyword_patterns
        changed.append(f"keyword_patterns ({len(keyword_patterns)})")
    if gate_message is not None:
        intent.gate_message = gate_message
        changed.append("gate_message")

    if not changed:
        return f"No fields provided to update on intent '{name}'."

    save_config(config)
    return f"Intent '{name}' updated: {', '.join(changed)}."


@mcp.tool()
async def remove_intent(name: str) -> str:
    """Remove an intent from the routing config.

    Use sparingly — removing a built-in intent (git_operation, session_end,
    task_start, etc.) will degrade the safety gates and routing accuracy.
    Prefer update_intent to modify behavior instead.

    Args:
        name: Intent name to remove
    """
    from .intent_config import load_config, save_config

    config = load_config()
    before = len(config.intents)
    config.intents = [i for i in config.intents if i.name != name]
    if len(config.intents) == before:
        return f"Intent '{name}' not found."
    save_config(config)
    return (
        f"Intent '{name}' removed. {len(config.intents)} intents remaining. "
        f"Hooks will reflect the change on the next user message."
    )


@mcp.tool()
async def compile_intent_to_skill(
    name: str,
    scope: str = "user",
    project_path: str | None = None,
) -> str:
    """Compile an MGCP intent into an Anthropic-format SKILL.md file.

    Walks intent → linked_workflow → ordered steps → lessons-per-step and
    renders all four layers into a single SKILL.md compatible with
    ~/.claude/skills/. The compiled skill is invocable as a slash command
    (/{intent_name}) and auto-discoverable by Claude via description matching.

    Important: this is NOT the deleted Phase 8 skill compilation. Compiling a
    skill does NOT remove the intent from the routing config, does NOT remove
    lessons from the active query pool, and does NOT change MGCP behavior.
    The skill is purely additive — the intent stays the source of truth, and
    the skill is a downstream artifact you can recompile any time.

    Args:
        name: Intent name to compile (must exist in intent_config.json)
        scope: 'user' writes to ~/.claude/skills/, 'project' writes to
            <project_path>/.claude/skills/ (defaults to cwd if scope=project
            and project_path is omitted)
        project_path: Required for project scope; absolute path to the
            project root whose .claude/skills/ directory should receive the file
    """
    from .skill_compiler import compile_intent_to_skill as _compile

    store, _, _ = await _ensure_initialized()

    try:
        result = await _compile(
            intent_name=name,
            store=store,
            scope=scope,
            project_path=Path(project_path) if project_path else None,
        )
    except ValueError as e:
        return f"Error: {e}"

    lines = [
        f"## Compiled skill: `{result.intent_name}`",
        "",
        f"**Path:** `{result.skill_path}`",
        f"**Bytes written:** {result.bytes_written}",
        f"**Scope:** {scope}",
        "",
        "### Sources",
        f"- Intent: `{result.intent_name}` (from `~/.mgcp/intent_config.json`)",
    ]
    if result.has_gate:
        lines.append("- Hard keyword gate from intent.gate_message → rendered as STOP preamble")
    if result.workflow_id:
        lines.append(
            f"- Linked workflow: `{result.workflow_id}` "
            f"({result.step_count} steps, {result.lesson_count} lessons inlined)"
        )
    else:
        lines.append("- No linked workflow — skill contains action template only")

    lines.extend([
        "",
        "### Next steps",
        f"- Invoke as a slash command: `/{result.intent_name}`",
        "- Or let Claude auto-discover via the skill's description matching",
        "- The intent in `intent_config.json` remains the source of truth — "
        "edit it and re-run this tool to refresh the skill",
    ])
    return "\n".join(lines)


# ============================================================================
# ENFORCEMENT RULES (PreToolUse)
# ============================================================================
#
# These tools CRUD the rules consumed by the generic PreToolUse hook
# evaluator (src/mgcp/hook_templates/pre-tool-dispatcher.py). The hook
# reads ~/.mgcp/enforcement_rules.json on every tool call — no server
# round-trip is needed — so edits via these tools take effect on the
# *next* tool call without restarting Claude Code.


@mcp.tool()
async def list_enforcement_rules() -> str:
    """List all enforcement rules from ~/.mgcp/enforcement_rules.json.

    Enforcement rules are data-driven gates applied by the PreToolUse hook.
    Each rule pairs a trigger (which tool calls it matches) with
    preconditions (what must be true for the call to proceed). If a
    matched rule's preconditions are unsatisfied, the hook denies the
    tool call.
    """
    from .enforcement import load_config

    config = load_config()
    if not config.rules:
        return "No enforcement rules configured."
    lines = [f"# Enforcement rules ({len(config.rules)})", ""]
    for r in config.rules:
        status = "enabled" if r.enabled else "disabled"
        trig = r.trigger.tool_name
        if r.trigger.command_match:
            trig += f" [{r.trigger.command_match.type}]"
        lines.append(f"- **{r.name}** ({status}) — trigger: `{trig}`, bypass_scope: `{r.bypass_scope}`")
        if r.description:
            lines.append(f"  - {r.description}")
    return "\n".join(lines)


@mcp.tool()
async def get_enforcement_rule(name: str) -> str:
    """Return the full definition of one enforcement rule as JSON."""
    from .enforcement import load_config

    config = load_config()
    for r in config.rules:
        if r.name == name:
            return json.dumps(r.model_dump(), indent=2)
    return f"No rule named '{name}'."


@mcp.tool()
async def add_enforcement_rule(
    name: str,
    trigger: dict,
    preconditions: list[dict],
    bypass_scope: str,
    deny_reason: str,
    description: str = "",
    enabled: bool = True,
) -> str:
    """Add a new enforcement rule.

    Args:
        name: Unique rule identifier (e.g. 'docs-coupling', 'no-git-force-push')
        trigger: dict with 'tool_name' and optional 'command_match'.
            Example: {"tool_name": "Bash",
                      "command_match": {"type": "git_subcommand",
                                        "subcommands": ["commit", "push"]}}
            command_match.type ∈ {"git_subcommand", "regex", "contains"}.
        preconditions: list of dicts. Each has "type" ∈
            {"tool_called_this_turn", "tool_not_called_this_turn",
             "staged_files_coupling", "tool_input_glob",
             "staged_files_forbid", "staged_content_forbid",
             "diff_budget", "staged_python_complexity",
             "commit_message_requires", "transcript_tool_called"}. The first two
            take "tool_name"; the coupling type takes "couplings" — a list
            of {"when_staged": [glob,...], "require_one_of": [glob,...]};
            tool_input_glob takes "field" (which tool_input key to read)
            and "deny_globs", and denies when any glob matches that field;
            staged_files_forbid takes "deny_globs" and refuses a staged path
            outright, asking for nothing in return; staged_content_forbid
            takes "patterns", a list of regular expressions, and is the only
            type that reads the staged text rather than paths, which is what
            a private name or an absolute home path needs because both arrive
            inside a sentence. Both of the last two read only the lines a
            commit ADDS, so neither can refuse the commit that removes the
            offending thing. Keep their patterns in the rules file rather
            than in a repository, so a list of private names is never itself
            published. The last four are the structured coding gates;
            `mgcp.enforcement.Precondition` documents every field, and the
            evaluator in hook_templates/pre-tool-dispatcher.py is the contract.
        bypass_scope: short token (e.g. 'git', 'docs') the user can name
            in MGCP_BYPASS:<scope> to disable this rule for one turn.
        deny_reason: text shown to the LLM when the rule blocks a call.
        description: optional prose explaining the rule's purpose.
        enabled: defaults to True; set False to keep the rule but skip it.
    """
    from .enforcement import (
        EnforcementRule,
        Precondition,
        Trigger,
        load_config,
        save_config,
    )

    config = load_config()
    if any(r.name == name for r in config.rules):
        return f"Rule '{name}' already exists. Use update_enforcement_rule to change it."

    try:
        rule = EnforcementRule(
            name=name,
            description=description,
            enabled=enabled,
            trigger=Trigger(**trigger),
            preconditions=[Precondition(**p) for p in preconditions],
            bypass_scope=bypass_scope,
            deny_reason=deny_reason,
        )
    except Exception as e:
        return f"Invalid rule definition: {e}"

    config.rules.append(rule)
    save_config(config)
    return f"Added rule '{name}'. Takes effect on next tool call."


@mcp.tool()
async def update_enforcement_rule(
    name: str,
    trigger: dict | None = None,
    preconditions: list[dict] | None = None,
    bypass_scope: str | None = None,
    deny_reason: str | None = None,
    description: str | None = None,
    enabled: bool | None = None,
) -> str:
    """Update fields on an existing enforcement rule.

    Any argument left as None preserves the current value. Pass the same
    shapes as add_enforcement_rule.
    """
    from .enforcement import (
        EnforcementRule,
        Precondition,
        Trigger,
        load_config,
        save_config,
    )

    config = load_config()
    for i, r in enumerate(config.rules):
        if r.name != name:
            continue
        try:
            updated = EnforcementRule(
                name=r.name,
                description=description if description is not None else r.description,
                enabled=enabled if enabled is not None else r.enabled,
                trigger=Trigger(**trigger) if trigger is not None else r.trigger,
                preconditions=(
                    [Precondition(**p) for p in preconditions]
                    if preconditions is not None
                    else r.preconditions
                ),
                bypass_scope=bypass_scope if bypass_scope is not None else r.bypass_scope,
                deny_reason=deny_reason if deny_reason is not None else r.deny_reason,
            )
        except Exception as e:
            return f"Invalid update: {e}"
        config.rules[i] = updated
        save_config(config)
        return f"Updated rule '{name}'."
    return f"No rule named '{name}'."


@mcp.tool()
async def sync_enforcement_rules() -> str:
    """Add shipped enforcement rules this install does not have yet. Add only.

    Nothing else delivers a rule added after you installed MGCP. The defaults
    seed on first install only, behind a check for a missing file, and
    load_config returns them only when the file is absent. So an operator who
    has used MGCP for a week never receives a rule shipped since.

    Add only, and never upsert. A populated rules file is a customised file:
    rules get disabled, retuned and renamed by hand, and a shipped rule of the
    same name may differ from its default on purpose. Overwriting one by name
    would silently revert that. A rule already present keeps its every field
    and its position, and the order of your rules never changes.

    Rules that arrive this way are the ones the package ships. The structured
    coding gates all ship disabled and in audit mode, so this call cannot start
    refusing your tool calls. Enable one with toggle_enforcement_rule when you
    have read what it would have refused.
    """
    from .enforcement import merge_missing_defaults

    try:
        result = merge_missing_defaults()
    except Exception as exc:
        return (f"Could not sync enforcement rules: {exc}\n"
                "The file was left untouched. A rules file that exists but "
                "does not parse is never overwritten, because the hook is "
                "still enforcing from it.")

    if not result["added"]:
        return (f"Nothing to add. All shipped rules are already in "
                f"{result['path']} ({result['kept']} rules).")
    lines = [
        f"Added {len(result['added'])} rule(s) to {result['path']}:",
        *(f"  {name}" for name in result["added"]),
        "",
        f"Your {result['kept']} existing rules were not touched.",
    ]
    if result["enabled"]:
        lines += [
            f"{len(result['enabled'])} of them are ON in audit mode, which "
            "records what each would have refused and refuses nothing.",
            f"Hook check: {result['hook_detail']}.",
            "Read the would_deny rows in the Enforcement view, then promote a "
            "rule with update_enforcement_rule(mode='enforce') when its "
            "refusals are ones you agree with.",
        ]
    else:
        lines += [
            "They are all DISABLED, because audit mode is only honoured by a "
            f"hook that knows the key. {result['hook_detail']}.",
            "Run `mgcp-init` to install the current hooks, then call this tool "
            "again to turn them on in audit mode.",
        ]
    return "\n".join(lines)


@mcp.tool()
async def remove_enforcement_rule(name: str) -> str:
    """Delete an enforcement rule by name."""
    from .enforcement import load_config, save_config

    config = load_config()
    before = len(config.rules)
    config.rules = [r for r in config.rules if r.name != name]
    if len(config.rules) == before:
        return f"No rule named '{name}'."
    save_config(config)
    return f"Removed rule '{name}'."


@mcp.tool()
async def toggle_enforcement_rule(name: str, enabled: bool) -> str:
    """Enable or disable a rule without deleting it."""
    from .enforcement import load_config, save_config

    config = load_config()
    for r in config.rules:
        if r.name == name:
            r.enabled = enabled
            save_config(config)
            return f"Rule '{name}' {'enabled' if enabled else 'disabled'}."
    return f"No rule named '{name}'."


@mcp.tool()
async def adjudicate_apology_gate(
    flagged_sentence: str, verdict: str, reasoning: str
) -> str:
    """Contest or confirm an apology-gate fire, on the record.

    The PreToolUse apology gate permits exactly two tools while armed:
    add_lesson (comply) and this one (adjudicate). Use this when the
    tripwire flagged text that is NOT you acknowledging your own error --
    quoted speech, error narration, a trigger word in a non-apology sense.

    Args:
        flagged_sentence: The sentence (or matched text) the gate flagged.
        verdict: "not_apology" -- false positive, the gate opens for this
                 turn; or "apology" -- genuine, the gate stays shut until
                 add_lesson is called.
        reasoning: Why. Required, minimum 20 characters. This is written to
                 the append-only audit log next to the flagged sentence,
                 where REM sampling and the human can review it. An
                 attestation is cheap to make and cheap to audit -- that
                 asymmetry is the design.
    """
    verdict = verdict.strip().lower()
    if verdict not in ("not_apology", "apology"):
        return "verdict must be 'not_apology' or 'apology'."
    if len(reasoning.strip()) < 20:
        return ("reasoning too short: state specifically why this is or is "
                "not you acknowledging your own error (>=20 chars).")

    base = Path(os.environ.get("MGCP_DATA_DIR", str(Path.home() / ".mgcp")))
    state_file = Path(
        os.environ.get("MGCP_STATE_FILE", str(base / "workflow_state.json"))
    )

    # The session this verdict speaks for. It is read from the state file, not
    # accepted as an argument: the hook requires an EXACT match against the
    # harness session_id, and nothing ever tells the model that value -- while
    # the gate is armed it cannot even read the transcript to look it up. So the
    # parameter could only ever be wrong, and every adjudication ever recorded
    # carried "" or an invented id and silently failed to open the gate.
    # UserPromptSubmit writes turn_session_id each turn.
    state = {}
    try:
        if state_file.exists():
            state = json.loads(state_file.read_text() or "{}")
        if not isinstance(state, dict):
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    session_id = state.get("turn_session_id") or ""

    entry = {
        "event": "adjudication",
        "gate": "apology",
        "verdict": verdict,
        "session_id": session_id,
        "flagged_sentence": flagged_sentence.strip()[:500],
        "reasoning": reasoning.strip()[:1000],
        "ts": datetime.now(UTC).isoformat(),
    }
    try:
        base.mkdir(parents=True, exist_ok=True)
        with open(base / "gate_audit.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        return f"Could not write the audit record ({exc}); adjudication NOT registered."

    # The hook reads this to open (or keep) the gate for the current turn;
    # UserPromptSubmit deletes it on the next user message.
    try:
        state["turn_apology_adjudication"] = {
            "verdict": verdict,
            "sentence": entry["flagged_sentence"],
            "session_id": session_id,
            "ts": entry["ts"],
        }
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2))
    except (OSError, json.JSONDecodeError) as exc:
        return f"Audit recorded, but gate state not updated ({exc}); the gate may still deny."

    if verdict == "not_apology":
        return (
            "Adjudication recorded: false positive. The gate is open for this "
            "turn. Your reasoning is on the audit record next to the flagged "
            "sentence."
        )
    return (
        "Adjudication recorded: genuine. The gate stays shut -- call "
        "mcp__mgcp__add_lesson now to capture what to do differently."
    )


# ============================================================================
# ENTRY POINT
# ============================================================================


def main():
    """Run the MCP server with stdio transport."""
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print("""MGCP - Memory Graph Core Primitives Server

Usage: mgcp [OPTIONS]

The MGCP server runs as an MCP (Model Context Protocol) server using stdio
transport. It is designed to be launched by MCP-compatible clients like
Claude Code, Cursor, or other LLM tools.

Options:
  -h, --help     Show this help message
  -V, --version  Show version number

To configure your LLM client to use MGCP:
  mgcp-init              Auto-detect and configure installed clients
  mgcp-init --list       Show supported clients
  mgcp-init --verify     Verify setup is working

Other commands:
  mgcp-bootstrap         Seed database with initial lessons
  mgcp-dashboard         Start the web dashboard
  mgcp-export            Export lessons to JSON
  mgcp-import            Import lessons from JSON
  mgcp-duplicates        Find duplicate lessons
  mgcp-backup            Back up / restore ~/.mgcp
  mgcp-migrate           Migrate ChromaDB data to Qdrant

Data is stored in ~/.mgcp/ by default.
""")
            return
        elif sys.argv[1] in ("--version", "-V"):
            from . import __version__
            print(f"mgcp {__version__}")
            return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
