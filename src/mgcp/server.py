"""MGCP - Memory Graph Control Protocol.

MCP server providing persistent, graph-based memory for LLM interactions.
Uses FastMCP for cleaner API - verified against official SDK docs.
https://github.com/modelcontextprotocol/python-sdk
"""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .catalogue_vector_store import CatalogueVectorStore
from .graph import LessonGraph
from .logging_config import configure_logging, get_logger
from .models import (
    ArchitecturalNote,
    Convention,
    Decision,
    Dependency,
    ErrorPattern,
    FileCoupling,
    GenericCatalogueItem,
    Lesson,
    ProjectContext,
    ProjectTodo,
    SecurityNote,
    Workflow,
    WorkflowStep,
    WorkflowStepLesson,
)
from .persistence import LessonStore
from .telemetry import TelemetryLogger
from .vector_store import VectorStore

# Configure logging with rotation (CRITICAL: logs to file, not stdout which breaks MCP STDIO)
configure_logging(console_output=False)
logger = get_logger("server")

# Initialize FastMCP server
mcp = FastMCP("mgcp")

# Global state (initialized on first tool call)
_store: LessonStore | None = None
_vector_store: VectorStore | None = None
_catalogue_vector: CatalogueVectorStore | None = None
_graph: LessonGraph | None = None
_telemetry: TelemetryLogger | None = None
_initialized = False
_init_lock = asyncio.Lock()

# Validation constants
VALID_RELATIONSHIP_TYPES = frozenset({
    "related", "prerequisite", "sequence_next", "alternative",
    "complements", "specializes", "generalizes", "contradicts"
})
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


async def _ensure_initialized() -> tuple[LessonStore, VectorStore, CatalogueVectorStore, LessonGraph, TelemetryLogger]:
    """Lazy initialization of components with thread-safe locking."""
    global _store, _vector_store, _catalogue_vector, _graph, _telemetry, _initialized

    # Fast path: already initialized
    if _initialized:
        return _store, _vector_store, _catalogue_vector, _graph, _telemetry

    # Slow path: acquire lock and initialize
    async with _init_lock:
        # Double-check after acquiring lock
        if _initialized:
            return _store, _vector_store, _catalogue_vector, _graph, _telemetry

        logger.info("Initializing MGCP server...")

        try:
            _store = LessonStore()
            _vector_store = VectorStore()
            _catalogue_vector = CatalogueVectorStore()
            _graph = LessonGraph()
            _telemetry = TelemetryLogger()

            # Load lessons from database
            lessons = await _store.get_all_lessons()
            logger.info(f"Loaded {len(lessons)} lessons from database")

            # Build graph
            _graph.load_from_lessons(lessons)

            # Sync vector store
            stored_ids = set(_vector_store.get_all_ids())
            for lesson in lessons:
                if lesson.id not in stored_ids:
                    _vector_store.add_lesson(lesson)

            # Sync catalogue vector store
            contexts = await _store.get_all_project_contexts()
            for ctx in contexts:
                _catalogue_vector.index_catalogue(ctx.project_id, ctx.catalogue)
            logger.info(f"Indexed catalogues for {len(contexts)} projects")

            # Start telemetry session
            await _telemetry.start_session()

            _initialized = True
            logger.info("Server initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize MGCP server: {e}")
            raise

        return _store, _vector_store, _catalogue_vector, _graph, _telemetry


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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()
    start_time = time.time()

    # Log the query
    query_id = await telemetry.log_query(task_description, source="tool")

    # Semantic search
    results = vector_store.search(task_description, limit=limit)

    if not results:
        return "No relevant lessons found. Consider adding lessons as you learn."

    # Fetch full lessons and record usage
    lessons = []
    scores = []
    for lesson_id, score in results:
        lesson = await store.get_lesson(lesson_id)
        if lesson:
            lessons.append(lesson)
            scores.append(score)
            await store.record_usage(lesson_id)

    # Log retrieval
    latency_ms = (time.time() - start_time) * 1000
    await telemetry.log_retrieve(
        query_id=query_id,
        lesson_ids=[l.id for l in lessons],
        scores=scores,
        latency_ms=latency_ms,
    )

    # Format response
    lines = [f"Found {len(lessons)} relevant lessons:\n"]
    for lesson, score in zip(lessons, scores):
        lines.append(lesson.to_context())
        lines.append(f"  (relevance: {score:.0%})\n")

    return "\n".join(lines)


@mcp.tool()
async def get_lesson(lesson_id: str) -> str:
    """Get full details of a specific lesson by ID.

    Args:
        lesson_id: The unique lesson identifier to retrieve
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    depth = min(depth, 5)  # Cap depth

    if lesson_id not in [n for n in graph.graph.nodes()]:
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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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

    # Add to vector store and graph
    vector_store.add_lesson(lesson)
    graph.add_lesson(lesson)

    # Log
    await telemetry.log_add(lesson.id, lesson.trigger)

    return f"Lesson '{lesson.id}' added successfully."


@mcp.tool()
async def refine_lesson(
    lesson_id: str,
    refinement: str,
    new_action: str = "",
) -> str:
    """Improve an existing lesson with new insight.

    Use when you've learned something that enhances a lesson.

    Args:
        lesson_id: ID of the lesson to refine
        refinement: What to add or improve - will be appended to rationale
        new_action: Updated action text (optional, leave empty to keep current)
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return f"Lesson not found: {lesson_id}"

    old_version = lesson.version

    # Update lesson
    if new_action:
        lesson.action = new_action

    # Append refinement to rationale
    version_note = f"\n\n[v{lesson.version + 1}] {refinement}"
    if lesson.rationale:
        lesson.rationale = lesson.rationale + version_note
    else:
        lesson.rationale = refinement

    lesson.version += 1
    lesson.last_refined = datetime.now(UTC)

    # Save
    await store.update_lesson(lesson)
    vector_store.add_lesson(lesson)  # Re-index

    # Log
    await telemetry.log_refine(lesson_id, old_version, lesson.version, refinement)

    return f"Lesson '{lesson_id}' refined to version {lesson.version}."


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

    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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

    # Add to lesson_a's relationships (avoid duplicates)
    existing_targets = [r.target for r in lesson_a.relationships]
    if lesson_id_b not in existing_targets:
        lesson_a.relationships.append(new_rel)
        # Also maintain legacy related_ids for backwards compatibility
        if lesson_id_b not in lesson_a.related_ids:
            lesson_a.related_ids.append(lesson_id_b)
        await store.update_lesson(lesson_a)

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

        existing_targets_b = [r.target for r in lesson_b.relationships]
        if lesson_id_a not in existing_targets_b:
            lesson_b.relationships.append(reverse_rel)
            if lesson_id_a not in lesson_b.related_ids:
                lesson_b.related_ids.append(lesson_id_a)
            await store.update_lesson(lesson_b)

    # Update graph
    graph.add_lesson(lesson_a)
    graph.add_lesson(lesson_b)

    # Format output
    arrow = "↔" if bidirectional else "→"
    type_str = f" ({relationship_type})" if relationship_type != "related" else ""
    return f"Linked '{lesson_id_a}' {arrow} '{lesson_id_b}'{type_str}"


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

    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    # Try to find by path
    context = await store.get_project_context_by_path(project_path)

    if not context:
        return f"No saved context for project at: {project_path}\nUse save_project_context to create one."

    # Update access time and session count
    context.last_accessed = datetime.now(UTC)
    context.session_count += 1
    context.last_session_id = telemetry.session_id
    await store.save_project_context(context)

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
    import hashlib
    from pathlib import Path

    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    # Generate project ID from path
    project_id = hashlib.sha256(project_path.encode()).hexdigest()[:12]

    # Get existing or create new
    context = await store.get_project_context(project_id)

    if context:
        # Update existing
        if notes:
            context.notes = notes
        if active_files:
            context.active_files = [f.strip() for f in active_files.split(",") if f.strip()]
        if decision:
            context.recent_decisions.append(decision)
            # Keep only last 10 decisions
            context.recent_decisions = context.recent_decisions[-10:]
        context.last_accessed = datetime.now(UTC)
        context.last_session_id = telemetry.session_id
    else:
        # Create new
        name = project_name or Path(project_path).name
        context = ProjectContext(
            project_id=project_id,
            project_name=name,
            project_path=project_path,
            notes=notes or None,
            active_files=[f.strip() for f in active_files.split(",") if f.strip()] if active_files else [],
            recent_decisions=[decision] if decision else [],
            last_session_id=telemetry.session_id,
            session_count=1,
        )

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
    import hashlib
    from pathlib import Path

    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    project_id = hashlib.sha256(project_path.encode()).hexdigest()[:12]
    context = await store.get_project_context(project_id)

    if not context:
        # Create minimal context
        from .models import ProjectContext
        context = ProjectContext(
            project_id=project_id,
            project_name=Path(project_path).name,
            project_path=project_path,
            last_session_id=telemetry.session_id,
        )

    # Add todo
    new_todo = ProjectTodo(
        content=todo,
        priority=min(max(priority, 0), 9),
        notes=notes or None,
    )
    context.todos.append(new_todo)
    context.last_accessed = datetime.now(UTC)

    await store.save_project_context(context)

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
    import hashlib

    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    project_id = hashlib.sha256(project_path.encode()).hexdigest()[:12]
    context = await store.get_project_context(project_id)

    if not context:
        return f"No project context found for: {project_path}"

    if todo_index < 0 or todo_index >= len(context.todos):
        return f"Invalid todo index. Project has {len(context.todos)} todos (0-{len(context.todos)-1})."

    todo = context.todos[todo_index]

    if status and status in ("pending", "in_progress", "completed", "blocked"):
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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
# RESOURCES
# ============================================================================


@mcp.resource("lessons://bootstrap")
async def get_bootstrap_lessons() -> str:
    """Critical lessons loaded at session start.

    Returns the top 10 most-used lessons for immediate context.
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    lessons = await store.get_all_lessons()
    top_lessons = sorted(lessons, key=lambda l: l.usage_count, reverse=True)[:10]

    if not top_lessons:
        return "No lessons available yet. Add lessons using add_lesson tool."

    lines = ["# Bootstrap Lessons\n"]
    for lesson in top_lessons:
        lines.append(lesson.to_context())
        lines.append("")

    return "\n".join(lines)


@mcp.resource("lessons://graph")
async def get_lesson_graph() -> str:
    """Full lesson graph structure as JSON.

    Returns nodes and edges for visualization.
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()
    return json.dumps(graph.to_dict(), indent=2)


@mcp.resource("lessons://stats")
async def get_statistics() -> str:
    """Usage statistics for all lessons.

    Returns analytics data in JSON format.
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    usage = await telemetry.get_lesson_usage()
    graph_stats = graph.get_statistics()

    stats = {
        "graph": graph_stats,
        "lesson_usage": usage[:20],  # Top 20
        "total_lessons": len(await store.get_all_lessons()),
    }

    return json.dumps(stats, indent=2)


# ============================================================================
# CATALOGUE TOOLS
# ============================================================================


async def _get_or_create_project_context(project_path: str) -> ProjectContext:
    """Get or create a project context by path."""
    import hashlib
    context = await _store.get_project_context_by_path(project_path)
    if context:
        return context

    # Create new context using pathlib for cross-platform compatibility
    project_id = hashlib.sha256(project_path.encode()).hexdigest()[:12]
    project_name = Path(project_path).name or "Unknown"
    context = ProjectContext(
        project_id=project_id,
        project_name=project_name,
        project_path=project_path,
    )
    await _store.save_project_context(context)
    return context


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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    project_id = None
    if project_path:
        context = await store.get_project_context_by_path(project_path)
        if context:
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
async def add_catalogue_arch_note(
    project_path: str,
    title: str,
    description: str,
    category: str = "architecture",
    related_files: str = "",
) -> str:
    """Add an architectural note to a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        title: Short title (e.g., 'MCP Server Restart Required')
        description: Full explanation
        category: One of: architecture, convention, gotcha, security, performance
        related_files: Comma-separated list of files this applies to
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    note = ArchitecturalNote(
        title=title,
        description=description,
        category=category,
        related_files=[f.strip() for f in related_files.split(",") if f.strip()],
    )

    context.catalogue.architecture_notes.append(note)
    await store.save_project_context(context)
    catalogue_vector._add_arch_note(context.project_id, note)

    return f"Added architectural note: {title}"


@mcp.tool()
async def add_catalogue_security_note(
    project_path: str,
    title: str,
    description: str,
    severity: str = "info",
    status: str = "open",
    mitigation: str = "",
) -> str:
    """Add a security note to a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        title: Issue title
        description: Details about the issue
        severity: One of: info, low, medium, high, critical
        status: One of: open, mitigated, accepted, resolved
        mitigation: How it's being addressed (optional)
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    note = SecurityNote(
        title=title,
        description=description,
        severity=severity,
        status=status,
        mitigation=mitigation or None,
    )

    context.catalogue.security_notes.append(note)
    await store.save_project_context(context)
    catalogue_vector._add_security_note(context.project_id, note)

    return f"Added security note: {title} [{severity}]"


@mcp.tool()
async def add_catalogue_dependency(
    project_path: str,
    name: str,
    purpose: str,
    dep_type: str = "library",
    version: str = "",
    docs_url: str = "",
    notes: str = "",
) -> str:
    """Add a dependency (framework, library, or tool) to a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        name: Package/library name
        purpose: What it's used for in this project
        dep_type: One of: framework, library, tool
        version: Version constraint or exact version
        docs_url: Link to documentation
        notes: Project-specific usage notes
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    dep = Dependency(
        name=name,
        purpose=purpose,
        version=version or None,
        docs_url=docs_url or None,
        notes=notes or None,
    )

    if dep_type == "framework":
        context.catalogue.frameworks.append(dep)
    elif dep_type == "tool":
        context.catalogue.tools.append(dep)
    else:
        context.catalogue.libraries.append(dep)

    await store.save_project_context(context)
    catalogue_vector._add_dependency(context.project_id, dep, dep_type)

    return f"Added {dep_type}: {name}"


@mcp.tool()
async def add_catalogue_convention(
    project_path: str,
    title: str,
    rule: str,
    category: str = "style",
    examples: str = "",
) -> str:
    """Add a coding convention to a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        title: Short title (e.g., 'Snake case for functions')
        rule: The actual rule to follow
        category: One of: naming, style, structure, testing, git
        examples: Comma-separated examples
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    conv = Convention(
        title=title,
        rule=rule,
        category=category,
        examples=[e.strip() for e in examples.split(",") if e.strip()],
    )

    context.catalogue.conventions.append(conv)
    await store.save_project_context(context)
    catalogue_vector._add_convention(context.project_id, conv)

    return f"Added convention: {title}"


@mcp.tool()
async def add_catalogue_coupling(
    project_path: str,
    files: str,
    reason: str,
    direction: str = "bidirectional",
) -> str:
    """Add a file coupling to a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        files: Comma-separated list of coupled files (e.g., 'server.py, models.py')
        reason: Why these files are coupled
        direction: One of: bidirectional, a_triggers_b
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    coupling = FileCoupling(
        files=[f.strip() for f in files.split(",") if f.strip()],
        reason=reason,
        direction=direction,
    )

    context.catalogue.file_couplings.append(coupling)
    await store.save_project_context(context)
    catalogue_vector._add_file_coupling(context.project_id, coupling)

    return f"Added file coupling: {' <-> '.join(coupling.files)}"


@mcp.tool()
async def add_catalogue_decision(
    project_path: str,
    title: str,
    decision: str,
    rationale: str,
    alternatives: str = "",
) -> str:
    """Add an architectural decision to a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        title: Short title (e.g., 'Chose NetworkX over Neo4j')
        decision: What was decided
        rationale: Why this choice was made (prevents re-litigating)
        alternatives: Comma-separated list of alternatives considered
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    dec = Decision(
        title=title,
        decision=decision,
        rationale=rationale,
        alternatives=[a.strip() for a in alternatives.split(",") if a.strip()],
    )

    context.catalogue.decisions.append(dec)
    await store.save_project_context(context)
    catalogue_vector._add_decision(context.project_id, dec)

    return f"Added decision: {title}"


@mcp.tool()
async def add_catalogue_error_pattern(
    project_path: str,
    error_signature: str,
    cause: str,
    solution: str,
    related_files: str = "",
) -> str:
    """Add an error pattern to a project's catalogue.

    Args:
        project_path: Absolute path to the project root directory
        error_signature: What the error looks like (text or regex)
        cause: Root cause of the error
        solution: How to fix it
        related_files: Comma-separated list of files where this error occurs
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    err = ErrorPattern(
        error_signature=error_signature,
        cause=cause,
        solution=solution,
        related_files=[f.strip() for f in related_files.split(",") if f.strip()],
    )

    context.catalogue.error_patterns.append(err)
    await store.save_project_context(context)
    catalogue_vector._add_error_pattern(context.project_id, err)

    return f"Added error pattern: {error_signature[:50]}..."


@mcp.tool()
async def add_catalogue_custom_item(
    project_path: str,
    item_type: str,
    title: str,
    content: str,
    metadata: str = "",
    tags: str = "",
) -> str:
    """Add a custom/flexible catalogue item to a project.

    Use this for item types not covered by built-in types (arch, security, etc.).
    Create any item type you need (e.g., 'api_endpoint', 'env_var', 'migration').

    Args:
        project_path: Absolute path to the project root directory
        item_type: Custom type name (e.g., 'api_endpoint', 'env_var', 'feature_flag')
        title: Short title for the item
        content: Main content/description
        metadata: Key-value pairs as 'key1=value1,key2=value2' (optional)
        tags: Comma-separated tags for searchability (optional)
    """
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await _get_or_create_project_context(project_path)

    # Parse metadata
    metadata_dict = {}
    if metadata:
        for pair in metadata.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                metadata_dict[key.strip()] = value.strip()

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    item = GenericCatalogueItem(
        item_type=item_type,
        title=title,
        content=content,
        metadata=metadata_dict,
        tags=tag_list,
    )

    context.catalogue.custom_items.append(item)
    await store.save_project_context(context)

    # Index in vector store for searchability
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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    context = await store.get_project_context_by_path(project_path)
    if not context:
        return f"Project not found: {project_path}"

    cat = context.catalogue
    removed = False

    if item_type == "arch":
        original_len = len(cat.architecture_notes)
        cat.architecture_notes = [n for n in cat.architecture_notes if n.title != identifier]
        removed = len(cat.architecture_notes) < original_len
    elif item_type == "security":
        original_len = len(cat.security_notes)
        cat.security_notes = [n for n in cat.security_notes if n.title != identifier]
        removed = len(cat.security_notes) < original_len
    elif item_type == "framework":
        original_len = len(cat.frameworks)
        cat.frameworks = [d for d in cat.frameworks if d.name != identifier]
        removed = len(cat.frameworks) < original_len
    elif item_type == "library":
        original_len = len(cat.libraries)
        cat.libraries = [d for d in cat.libraries if d.name != identifier]
        removed = len(cat.libraries) < original_len
    elif item_type == "tool":
        original_len = len(cat.tools)
        cat.tools = [d for d in cat.tools if d.name != identifier]
        removed = len(cat.tools) < original_len
    elif item_type == "convention":
        original_len = len(cat.conventions)
        cat.conventions = [c for c in cat.conventions if c.title != identifier]
        removed = len(cat.conventions) < original_len
    elif item_type == "coupling":
        original_len = len(cat.file_couplings)
        cat.file_couplings = [c for c in cat.file_couplings if identifier not in c.files]
        removed = len(cat.file_couplings) < original_len
    elif item_type == "decision":
        original_len = len(cat.decisions)
        cat.decisions = [d for d in cat.decisions if d.title != identifier]
        removed = len(cat.decisions) < original_len
    elif item_type == "error":
        original_len = len(cat.error_patterns)
        cat.error_patterns = [e for e in cat.error_patterns if not e.error_signature.startswith(identifier)]
        removed = len(cat.error_patterns) < original_len
    elif item_type == "custom" or item_type not in (
        "arch", "security", "framework", "library", "tool", "convention",
        "coupling", "decision", "error"
    ):
        # Handle custom items - identifier can be "type:title" or just "title"
        if ":" in identifier:
            custom_type, custom_title = identifier.split(":", 1)
            original_len = len(cat.custom_items)
            cat.custom_items = [
                i for i in cat.custom_items
                if not (i.item_type == custom_type and i.title == custom_title)
            ]
            removed = len(cat.custom_items) < original_len
        else:
            # Search by title only
            original_len = len(cat.custom_items)
            cat.custom_items = [i for i in cat.custom_items if i.title != identifier]
            removed = len(cat.custom_items) < original_len

    if removed:
        await store.save_project_context(context)
        catalogue_vector.remove_item(context.project_id, item_type, identifier)
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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
        return f"Unknown item type: {item_type}"

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

    workflows = await store.get_all_workflows()
    if not workflows:
        return "No workflows available."

    # Get or create workflows collection
    workflows_collection = vector_store.client.get_or_create_collection(
        name="workflows",
        metadata={"hnsw:space": "cosine"},
    )

    # Index/update workflows in collection
    for wf in workflows:
        # Combine name, description, and trigger for semantic matching
        searchable_text = f"{wf.name}. {wf.description}. Keywords: {wf.trigger}"
        workflows_collection.upsert(
            ids=[wf.id],
            documents=[searchable_text],
            metadatas=[{
                "name": wf.name,
                "description": wf.description,
                "trigger": wf.trigger,
                "step_count": len(wf.steps),
            }],
        )

    # Query for matching workflows
    results = workflows_collection.query(
        query_texts=[task_description],
        n_results=len(workflows),
        include=["documents", "metadatas", "distances"],
    )

    if not results["ids"] or not results["ids"][0]:
        return "No workflows matched."

    matches = []
    for i, wf_id in enumerate(results["ids"][0]):
        # ChromaDB returns distance, convert to similarity (1 - distance for cosine)
        distance = results["distances"][0][i]
        relevance = 1 - distance

        if relevance >= min_relevance:
            metadata = results["metadatas"][0][i]
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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
    store, vector_store, catalogue_vector, graph, telemetry = await _ensure_initialized()

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
# ENTRY POINT
# ============================================================================


def main():
    """Run the MCP server with stdio transport."""
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print("""MGCP - Memory Graph Control Protocol Server

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

Data is stored in ~/.mgcp/ by default.
""")
            return
        elif sys.argv[1] in ("--version", "-V"):
            print("mgcp 1.0.0")
            return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
