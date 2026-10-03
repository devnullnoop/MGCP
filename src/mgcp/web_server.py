"""MGCP web server for telemetry visualization and REST API."""

import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .embedding import daemon_status
from .graph import LessonGraph
from .models import ProjectContext, ProjectTodo
from .persistence import LessonStore, StaleWriteError
from .qdrant_vector_store import (
    QdrantVectorStore,
    get_default_qdrant_path,
    get_qdrant_url,
)
from .telemetry import TelemetryLogger

logger = logging.getLogger(__name__)

# Global instances
telemetry: TelemetryLogger | None = None
store: LessonStore | None = None
vector_store: QdrantVectorStore | None = None
catalogue_vector = None  # QdrantCatalogueStore, initialized lazily
graph: LessonGraph | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize stores on startup."""
    global telemetry, store, vector_store, graph

    telemetry = TelemetryLogger()
    store = LessonStore()
    graph = LessonGraph()
    # Qdrant is opened on demand, not here: see get_vector_store().

    # Load existing lessons into graph
    lessons = await store.get_all_lessons()
    for lesson in lessons:
        graph.add_lesson(lesson)

    logger.info(f"Web server initialized with {len(lessons)} lessons")

    yield

    logger.info("Shutting down web server")


app = FastAPI(
    title="MGCP Web API",
    description="""
## Memory Graph Core Primitives - Web API

REST API for managing lessons, projects, and viewing telemetry.

### Key Endpoints

- **Lessons**: CRUD operations for lessons (`/api/lessons`)
- **Projects**: Project context management (`/api/projects`)
- **Catalogue**: Project-specific knowledge (`/api/projects/{project_id}/catalogue`)
- **Telemetry**: Session and usage data (`/api/sessions`, `/api/timeline`)
- **Intents & Skills**: Intent routing config and skill compilation
  (`/api/intent-config`, `/api/intent-config/intents/{name}/skill-status`,
  `/api/intent-config/intents/{name}/compile`)

### Analytics

Read-only views over the three stores, used by the instrument panel:
`/api/signal`, `/api/retrieval/timeseries`, `/api/retrieval/misses`,
`/api/effectiveness`, `/api/gate-audit`, `/api/enforcement/rules`,
`/api/rem/state`, `/api/soliloquies`

### UI

The instrument panel is one document served at `/`, routed client-side on the
hash (`#/signal`, `#/enforcement`, ...). Unknown top-level paths fall through to
it so old bookmarks still land somewhere, which is why no page path is listed
here as an endpoint — none of them resolve to a route of their own.
`/docs` is this API documentation.
""",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# REST API Endpoints
# ============================================================================

async def ensure_initialized():
    """Ensure stores are initialized (for TestClient compatibility)."""
    global telemetry, store, vector_store, graph
    if store is None:
        telemetry = TelemetryLogger()
        store = LessonStore()
        graph = LessonGraph()
        lessons = await store.get_all_lessons()
        for lesson in lessons:
            graph.add_lesson(lesson)


def get_vector_store() -> QdrantVectorStore:
    """Open the vector store on first use, not at startup.

    Local-mode Qdrant permits one client per path, so opening it eagerly meant
    the dashboard could not run at all while an MCP server held the lock — even
    though nothing it displays needs vectors. Every analytics view reads
    lessons.db, telemetry.db and gate_audit.jsonl; only lesson create/update/
    delete touch Qdrant. Those few routes now pay the cost, and they fail with
    a message that says what is holding the store rather than preventing the
    server from starting.
    """
    global vector_store
    if vector_store is None:
        try:
            from .qdrant_server import ensure_running_if_configured

            ensure_running_if_configured()
            vector_store = QdrantVectorStore()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"The vector store is unavailable: {exc}. Local-mode Qdrant allows one "
                    "client per path, so another MGCP process (usually the MCP server) is "
                    "holding it. Read-only views work regardless; writing a lesson needs it."
                ),
            ) from exc
    return vector_store


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint with detailed system status."""
    await ensure_initialized()

    lessons = await store.get_all_lessons() if store else []
    projects = await store.get_all_project_contexts() if store else []

    return {
        "status": "healthy",
        "lessons_count": len(lessons),
        "projects_count": len(projects),
        "vector_store": "connected" if vector_store else "lazy (opened on first write)",
        # Which store a process is talking to is not obvious once two modes
        # exist, and a misconfigured session writing to the other one would be
        # silent. Report it.
        "qdrant_mode": "server" if get_qdrant_url() else "embedded",
        "qdrant_target": get_qdrant_url() or get_default_qdrant_path(),
        "catalogue_store": "connected" if catalogue_vector else "not initialized",
        # Whether this process holds its own 478 MiB copy of BGE or is sharing
        # one is the whole substance of v3 workstream B, and it is invisible
        # otherwise.
        "embedding": daemon_status(),
        "telemetry": "enabled" if telemetry else "disabled",
    }


@app.get("/api/lessons")
async def get_all_lessons() -> list[dict[str, Any]]:
    """Get all lessons."""
    await ensure_initialized()
    lessons = await store.get_all_lessons()
    return [lesson.model_dump(mode="json") for lesson in lessons]


@app.get("/api/lessons/usage")
async def get_lesson_usage() -> list[dict[str, Any]]:
    """Get usage stats for all lessons (for heatmap visualization)."""
    await ensure_initialized()
    return await telemetry.get_lesson_usage()


@app.get("/api/graph")
async def get_graph_data() -> dict[str, Any]:
    """Get full graph structure for visualization."""
    await ensure_initialized()

    lessons = await store.get_all_lessons()
    usage_data = await telemetry.get_lesson_usage() if telemetry else []
    usage_map = {u["lesson_id"]: u["total_retrievals"] for u in usage_data}

    # Find max usage for normalization
    max_usage = max(usage_map.values()) if usage_map else 1

    nodes = []
    links = []

    # Add root node
    nodes.append({
        "id": "root",
        "label": "Global Lessons",
        "type": "root",
        "usage": 100,
    })

    # Track categories we've seen
    categories = set()

    for lesson in lessons:
        # Add category node if lesson has a parent
        if lesson.parent_id and lesson.parent_id not in categories:
            parent_lesson = await store.get_lesson(lesson.parent_id)
            if parent_lesson:
                parent_usage = usage_map.get(lesson.parent_id, 0)
                nodes.append({
                    "id": lesson.parent_id,
                    "label": parent_lesson.trigger.split(",")[0].strip().title(),
                    "type": "category",
                    "parent": "root",
                    "usage": int((parent_usage / max_usage) * 100) if max_usage > 0 else 0,
                    "action": parent_lesson.action,
                })
                links.append({"source": "root", "target": lesson.parent_id})
                categories.add(lesson.parent_id)

        # Add lesson node
        usage = usage_map.get(lesson.id, 0)
        nodes.append({
            "id": lesson.id,
            "label": lesson.id.replace("-", " ").title(),
            "type": "lesson" if lesson.parent_id else "category",
            "parent": lesson.parent_id or "root",
            "usage": int((usage / max_usage) * 100) if max_usage > 0 else 0,
            "action": lesson.action,
            "trigger": lesson.trigger,
            "tags": lesson.tags,
        })

        # Add parent-child link
        if lesson.parent_id:
            links.append({
                "source": lesson.parent_id,
                "target": lesson.id,
                "relation": "parent",
                "weight": 1.0,
            })
        elif lesson.id not in categories:
            links.append({
                "source": "root",
                "target": lesson.id,
                "relation": "parent",
                "weight": 1.0,
            })

        # Add typed relationship links (new system)
        for rel in lesson.relationships:
            links.append({
                "source": lesson.id,
                "target": rel.target,
                "relation": rel.type,
                "weight": rel.weight,
                "context": rel.context,
                "bidirectional": rel.bidirectional,
            })

    # Add workflow nodes and their lesson connections
    workflows = await store.get_all_workflows()
    for workflow in workflows:
        # Add workflow root node
        workflow_root_id = f"wf-{workflow.id}"
        nodes.append({
            "id": workflow_root_id,
            "label": workflow.name,
            "type": "workflow",
            "usage": 50,  # Neutral usage
            "action": workflow.description,
        })
        # Connect workflow root to global root
        links.append({
            "source": "root",
            "target": workflow_root_id,
            "relation": "workflow",
            "weight": 0.8,
        })

        # Add workflow steps as nodes
        for step in workflow.steps:
            step_id = f"wf-{workflow.id}-{step.id}"
            nodes.append({
                "id": step_id,
                "label": f"{step.order}. {step.name}",
                "type": "workflow_step",
                "usage": 30,
                "action": step.description,
                "order": step.order,
            })
            # Connect step to workflow root or previous step
            if step.order == 1:
                links.append({
                    "source": workflow_root_id,
                    "target": step_id,
                    "relation": "workflow_step",
                    "weight": 1.0,
                })
            else:
                prev_step_id = f"wf-{workflow.id}-{workflow.steps[step.order - 2].id}"
                links.append({
                    "source": prev_step_id,
                    "target": step_id,
                    "relation": "workflow_step",
                    "weight": 1.0,
                })

            # Connect step to its linked lessons
            for lesson_link in step.lessons:
                links.append({
                    "source": step_id,
                    "target": lesson_link.lesson_id,
                    "relation": "step_lesson",
                    "weight": 0.8 if lesson_link.priority == 1 else 0.5,
                })

    # Add community_id to lesson nodes if graph has enough data
    try:
        communities = graph.detect_communities()
        lesson_to_community = {}
        for c in communities:
            for member_id in c["members"]:
                lesson_to_community[member_id] = c["community_id"]

        # Annotate nodes with community_id
        for node in nodes:
            if node["id"] in lesson_to_community:
                node["community"] = lesson_to_community[node["id"]]
    except Exception as e:
        logger.warning(f"Failed to annotate communities on graph: {e}")

    return {"nodes": nodes, "links": links}


@app.get("/api/communities")
async def get_communities(resolution: float = 1.0) -> list[dict[str, Any]]:
    """Get auto-detected lesson communities with optional summaries."""
    await ensure_initialized()

    communities = graph.detect_communities(resolution=resolution)

    # Enrich with summaries from DB
    for c in communities:
        summary = await store.get_community_summary(c["community_id"])
        if summary:
            c["summary"] = summary.model_dump(mode="json")
            # Staleness check
            current_members = set(c["members"])
            snapshot_members = set(summary.member_ids)
            c["is_stale"] = current_members != snapshot_members
        else:
            c["summary"] = None
            c["is_stale"] = False

    return communities


@app.get("/api/sessions")
async def get_sessions(limit: int = 20) -> list[dict[str, Any]]:
    """Get recent sessions with stats."""
    if not telemetry:
        return []
    return await telemetry.get_session_history(limit)


@app.get("/api/sessions/{session_id}/events")
async def get_session_events(session_id: str) -> list[dict[str, Any]]:
    """Get all events for a specific session."""
    if not telemetry:
        return []
    return await telemetry.get_session_events(session_id)


@app.get("/api/timeline")
async def get_timeline(hours: int = 1) -> list[dict[str, Any]]:
    """Get query timeline data."""
    if not telemetry:
        return []

    events = []
    sessions = await telemetry.get_session_history(100)

    for session in sessions:
        session_events = await telemetry.get_session_events(session["id"])
        for event in session_events:
            if event["event_type"] in ("query", "retrieve"):
                events.append({
                    "timestamp": event["timestamp"],
                    "type": event["event_type"],
                    "session_id": session["id"],
                    "payload": event["payload"],
                })

    return sorted(events, key=lambda x: x["timestamp"])


@app.get("/api/queries/common")
async def get_common_queries(limit: int = 20) -> list[dict[str, Any]]:
    """Get most common queries."""
    if not telemetry:
        return []
    return await telemetry.get_common_queries(limit)


# ============================================================================
# Project Context API
# ============================================================================


@app.get("/api/projects")
async def get_all_projects() -> list[dict[str, Any]]:
    """Get all project contexts."""
    await ensure_initialized()
    contexts = await store.get_all_project_contexts()
    return [ctx.model_dump(mode="json") for ctx in contexts]


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any] | None:
    """Get a specific project context."""
    await ensure_initialized()
    ctx = await store.get_project_context(project_id)
    return ctx.model_dump(mode="json") if ctx else None


@app.post("/api/projects")
async def create_project(data: dict[str, Any]) -> dict[str, Any]:
    """Create a new project context."""
    await ensure_initialized()

    project_path = data.get("project_path", "")
    project_name = data.get("project_name", "") or project_path.split("/")[-1]

    if not project_path:
        return {"error": "project_path is required"}

    project_id = hashlib.sha256(project_path.encode()).hexdigest()[:12]

    # Check if already exists
    existing = await store.get_project_context(project_id)
    if existing:
        return {"error": "Project already exists", "project_id": project_id}

    ctx = ProjectContext(
        project_id=project_id,
        project_name=project_name,
        project_path=project_path,
    )
    await store.save_project_context(ctx)

    return ctx.model_dump(mode="json")


@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Update a project context."""
    await ensure_initialized()

    ctx = await store.get_project_context(project_id)
    if not ctx:
        return {"error": "Project not found"}

    # Update fields
    if "project_name" in data:
        ctx.project_name = data["project_name"]
    if "notes" in data:
        ctx.notes = data["notes"] or None
    if "active_files" in data:
        ctx.active_files = data["active_files"]
    if "recent_decisions" in data:
        ctx.recent_decisions = data["recent_decisions"]
    if "todos" in data:
        ctx.todos = [ProjectTodo(**t) for t in data["todos"]]

    from datetime import UTC, datetime
    ctx.last_accessed = datetime.now(UTC)

    await store.save_project_context(ctx)
    return ctx.model_dump(mode="json")


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    """Delete a project context."""
    await ensure_initialized()

    ctx = await store.get_project_context(project_id)
    if not ctx:
        return {"error": "Project not found"}

    # Delete from database
    async with store._connection(commit=True) as conn:
        await conn.execute("DELETE FROM project_contexts WHERE project_id = ?", (project_id,))

    return {"deleted": project_id}


# ============================================================================
# Project Catalogue API
# ============================================================================


@app.get("/api/projects/{project_id}/catalogue")
async def get_project_catalogue(project_id: str) -> dict[str, Any]:
    """Get full catalogue for a project."""
    await ensure_initialized()

    ctx = await store.get_project_context(project_id)
    if not ctx:
        return {"error": "Project not found"}

    return ctx.catalogue.model_dump(mode="json")


@app.post("/api/projects/{project_id}/catalogue/{item_type}")
async def add_catalogue_item(project_id: str, item_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Add a catalogue item to a project.

    item_type: arch, security, framework, library, tool, convention, coupling, decision, error
    """
    await ensure_initialized()
    from .models import (
        ArchitecturalNote,
        Convention,
        Decision,
        Dependency,
        ErrorPattern,
        FileCoupling,
        SecurityNote,
    )

    ctx = await store.get_project_context(project_id)
    if not ctx:
        return {"error": "Project not found"}

    cat = ctx.catalogue
    item = None

    if item_type == "arch":
        item = ArchitecturalNote(**data)
        cat.architecture_notes.append(item)
    elif item_type == "security":
        item = SecurityNote(**data)
        cat.security_notes.append(item)
    elif item_type == "framework":
        item = Dependency(**data)
        cat.frameworks.append(item)
    elif item_type == "library":
        item = Dependency(**data)
        cat.libraries.append(item)
    elif item_type == "tool":
        item = Dependency(**data)
        cat.tools.append(item)
    elif item_type == "convention":
        item = Convention(**data)
        cat.conventions.append(item)
    elif item_type == "coupling":
        item = FileCoupling(**data)
        cat.file_couplings.append(item)
    elif item_type == "decision":
        item = Decision(**data)
        cat.decisions.append(item)
    elif item_type == "error":
        item = ErrorPattern(**data)
        cat.error_patterns.append(item)
    else:
        return {"error": f"Unknown item type: {item_type}"}

    await store.save_project_context(ctx)

    if telemetry:
        await telemetry.log_event(
            "catalogue_update",
            {"project_id": project_id, "item_type": item_type, "action": "add"},
        )

    return {"added": item.model_dump(mode="json") if item else None}


@app.delete("/api/projects/{project_id}/catalogue/{item_type}/{identifier}")
async def remove_catalogue_item_endpoint(
    project_id: str, item_type: str, identifier: str
) -> dict[str, Any]:
    """Remove a catalogue item from a project.

    identifier: title (for notes/decisions), name (for dependencies), or index (for couplings)
    """
    await ensure_initialized()

    ctx = await store.get_project_context(project_id)
    if not ctx:
        return {"error": "Project not found"}

    cat = ctx.catalogue
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
        # For couplings, identifier is the index
        try:
            idx = int(identifier)
            if 0 <= idx < len(cat.file_couplings):
                cat.file_couplings.pop(idx)
                removed = True
        except ValueError:
            pass
    elif item_type == "decision":
        original_len = len(cat.decisions)
        cat.decisions = [d for d in cat.decisions if d.title != identifier]
        removed = len(cat.decisions) < original_len
    elif item_type == "error":
        # For errors, match by error_signature
        original_len = len(cat.error_patterns)
        cat.error_patterns = [e for e in cat.error_patterns if e.error_signature != identifier]
        removed = len(cat.error_patterns) < original_len

    if removed:
        await store.save_project_context(ctx)
        return {"removed": identifier, "type": item_type}
    else:
        return {"error": f"Item not found: {identifier}"}


# ============================================================================
# Lesson CRUD API
# ============================================================================


@app.get("/api/lessons/{lesson_id}")
async def get_single_lesson(lesson_id: str) -> dict[str, Any] | None:
    """Get a specific lesson with full details."""
    await ensure_initialized()
    lesson = await store.get_lesson(lesson_id)
    return lesson.model_dump(mode="json") if lesson else None


@app.put("/api/lessons/{lesson_id}")
async def update_lesson(lesson_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Update a lesson."""
    await ensure_initialized()

    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return {"error": "Lesson not found"}

    # Update allowed fields
    if "trigger" in data:
        lesson.trigger = data["trigger"]
    if "action" in data:
        lesson.action = data["action"]
    if "rationale" in data:
        lesson.rationale = data["rationale"] or None
    if "tags" in data:
        lesson.tags = data["tags"]

    # Increment version on edit; the pre-edit value is the CAS token.
    expected_version = lesson.version
    lesson.version += 1
    from datetime import UTC, datetime
    lesson.last_refined = datetime.now(UTC)

    try:
        await store.update_lesson(lesson, expected_version=expected_version)
    except StaleWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Re-index in vector store
    get_vector_store().add_lesson(lesson)

    return lesson.model_dump(mode="json")


@app.delete("/api/lessons/{lesson_id}")
async def delete_lesson_endpoint(lesson_id: str) -> dict[str, Any]:
    """Delete a lesson."""
    await ensure_initialized()

    lesson = await store.get_lesson(lesson_id)
    if not lesson:
        return {"error": "Lesson not found"}

    # Delete from all stores
    deleted = await store.delete_lesson(lesson_id)
    if deleted:
        get_vector_store().remove_vector_lesson(lesson_id)
        graph.remove_graph_lesson(lesson_id)

    return {"deleted": lesson_id}


# ============================================================================
# Static Files & Dashboard
# ============================================================================

# Get the static directory path (web assets)
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def serve_app():
    """Serve the instrument panel.

    One app, seven views, client-side routed on the hash. It replaced eight
    separate pages that each answered "what is stored" and none of which
    covered enforcement, REM, the gate audit or the journal.
    """
    index = STATIC_DIR / "app" / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        "<!DOCTYPE html><html><body><h1>UI not installed.</h1>"
        "<p>Expected <code>static/app/index.html</code>.</p></body></html>",
        status_code=500,
    )


@app.get("/{view}", include_in_schema=False)
async def serve_app_view(view: str):
    """Deep links land on the same document; the hash router picks the view.

    Anything that is not a known view falls through to the app rather than a
    404, because the old page paths (/lessons, /projects, /intents...) are in
    people's history and bookmarks.
    """
    if view.startswith("api") or view in {"docs", "openapi.json", "redoc", "static", "ws"}:
        raise HTTPException(status_code=404, detail="Not found")
    return await serve_app()


# ============================================================================
# Intent Config API (routing prompt as data)
# ============================================================================

@app.get("/api/intent-config")
async def get_intent_config_api() -> dict[str, Any]:
    """Return the current intent routing config.

    Reads from ~/.mgcp/intent_config.json (or MGCP_DATA_DIR override). This
    is the same file the Claude Code hooks and REM intent_calibration read.
    """
    from .intent_config import load_config

    config = load_config()
    return config.to_disk_dict()


@app.post("/api/intent-config/intents")
async def add_intent_api(data: dict[str, Any]) -> dict[str, Any]:
    """Add a new intent to the routing config.

    Body: {name, description, action, tags?, keyword_patterns?, gate_message?}
    """
    from .intent_config import IntentDefinition, load_config, save_config

    name = data.get("name")
    if not name:
        return {"error": "name is required"}

    config = load_config()
    if any(i.name == name for i in config.intents):
        return {"error": f"intent '{name}' already exists"}

    keyword_patterns = data.get("keyword_patterns") or []
    gate_message = data.get("gate_message")
    if keyword_patterns and not gate_message:
        return {"error": "keyword_patterns require a gate_message"}

    try:
        intent = IntentDefinition(
            name=name,
            description=data.get("description", ""),
            action=data.get("action", ""),
            tags=data.get("tags") or [],
            linked_workflow=data.get("linked_workflow") or None,
            keyword_patterns=keyword_patterns,
            gate_message=gate_message,
        )
    except Exception as e:
        return {"error": f"validation failed: {e}"}

    config.intents.append(intent)
    save_config(config)
    return {"status": "added", "name": name, "total_intents": len(config.intents)}


@app.put("/api/intent-config/intents/{name}")
async def update_intent_api(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Update an existing intent. Only fields present in the body are changed."""
    from .intent_config import load_config, save_config

    config = load_config()
    intent = next((i for i in config.intents if i.name == name), None)
    if not intent:
        return {"error": f"intent '{name}' not found"}

    if "description" in data:
        intent.description = data["description"]
    if "action" in data:
        intent.action = data["action"]
    if "tags" in data:
        intent.tags = data["tags"] or []
    if "linked_workflow" in data:
        intent.linked_workflow = data["linked_workflow"] or None
    if "keyword_patterns" in data:
        intent.keyword_patterns = data["keyword_patterns"] or []
    if "gate_message" in data:
        intent.gate_message = data["gate_message"]

    save_config(config)
    return {"status": "updated", "name": name}


@app.delete("/api/intent-config/intents/{name}")
async def remove_intent_api(name: str) -> dict[str, Any]:
    """Remove an intent from the routing config."""
    from .intent_config import load_config, save_config

    config = load_config()
    before = len(config.intents)
    config.intents = [i for i in config.intents if i.name != name]
    if len(config.intents) == before:
        return {"error": f"intent '{name}' not found"}
    save_config(config)
    return {"status": "removed", "name": name, "total_intents": len(config.intents)}


@app.post("/api/intent-config/intents/{name}/compile")
async def compile_intent_api(name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compile an intent to a SKILL.md file at ~/.claude/skills/ or .claude/skills/.

    Body (optional): {scope?: 'user'|'project', project_path?: str}
    """
    from .skill_compiler import compile_intent_to_skill

    data = data or {}
    scope = data.get("scope", "user")
    project_path = data.get("project_path")

    await ensure_initialized()
    try:
        result = await compile_intent_to_skill(
            intent_name=name,
            store=store,
            scope=scope,
            project_path=Path(project_path) if project_path else None,
        )
    except ValueError as e:
        return {"error": str(e)}

    return {
        "status": "compiled",
        "intent_name": result.intent_name,
        "skill_path": str(result.skill_path),
        "bytes_written": result.bytes_written,
        "scope": scope,
        "workflow_id": result.workflow_id,
        "step_count": result.step_count,
        "lesson_count": result.lesson_count,
        "has_gate": result.has_gate,
    }


@app.get("/api/intent-config/intents/{name}/skill-status")
async def intent_skill_status_api(name: str, scope: str = "user") -> dict[str, Any]:
    """Return whether a compiled skill exists for this intent and whether it's stale.

    Used by the /intents page to badge intents that have a compiled skill
    and to flag when one of the backing lessons has been refined since the
    skill was generated.
    """
    from .skill_compiler import find_skill_path, is_skill_stale

    await ensure_initialized()
    path = find_skill_path(name, scope=scope)
    if path is None:
        return {"name": name, "scope": scope, "compiled": False, "stale": None}
    stale = await is_skill_stale(name, store, scope=scope)
    return {
        "name": name,
        "scope": scope,
        "compiled": True,
        "skill_path": str(path),
        "stale": stale,
    }


# ============================================================================
# Analytics — the instrument panel's data
#
# These read the three stores the dashboard needs together: telemetry.db for
# retrieval behaviour, lessons.db for the corpus and REM schedule, and
# gate_audit.jsonl for enforcement. They live here rather than in a module of
# their own because the dashboard is their only consumer.
#
# The load-bearing distinction throughout: a retrieved slot logged with score
# 0.0 was appended by the community bridge, not matched by relevance. Averaging
# the two together is what made per-lesson mean scores meaningless before
# v2.13, so every figure below keeps them apart.
# ============================================================================

BRIDGED_SENTINEL = 0.0
DEFAULT_MISS_THRESHOLD = 0.45


def _gate_audit_path() -> Path:
    base = os.environ.get("MGCP_DATA_DIR") or str(Path.home() / ".mgcp")
    return Path(base) / "gate_audit.jsonl"


def _read_gate_audit() -> list[dict[str, Any]]:
    """Every audit line we can parse. A corrupt line is skipped, not fatal."""
    path = _gate_audit_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            rows.append(entry)
    return rows


async def _retrieval_pairs() -> list[dict[str, Any]]:
    """Join every `retrieve` event back to the `query` that caused it.

    The link is `payload.query_id`, so this is a JSON join. Retrieves whose
    query row is missing are dropped rather than reported with an empty query.
    """
    conn = await telemetry._get_conn()
    cursor = await conn.execute(
        "SELECT id, timestamp, json_extract(payload,'$.query_text') AS q "
        "FROM events WHERE event_type='query'"
    )
    queries = {r[0]: {"ts": r[1], "text": r[2] or ""} for r in await cursor.fetchall()}

    cursor = await conn.execute(
        "SELECT json_extract(payload,'$.query_id') AS qid, payload "
        "FROM events WHERE event_type='retrieve' ORDER BY timestamp"
    )
    pairs = []
    for qid, payload in await cursor.fetchall():
        q = queries.get(qid)
        if not q:
            continue
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            continue
        ids = body.get("lesson_ids") or []
        scores = body.get("scores") or []
        matched = [s for s in scores if s > BRIDGED_SENTINEL]
        pairs.append({
            "ts": q["ts"],
            "query": q["text"],
            "lesson_ids": ids,
            "scores": scores,
            "matched": len(matched),
            "bridged": sum(1 for s in scores if s == BRIDGED_SENTINEL),
            "top": max(matched) if matched else None,
            "latency_ms": body.get("latency_ms"),
        })
    return pairs


@app.get("/api/signal")
async def get_signal(miss_threshold: float = DEFAULT_MISS_THRESHOLD) -> dict[str, Any]:
    """Headline vitals: is the memory working, and where is it not."""
    await ensure_initialized()
    pairs = await _retrieval_pairs()
    scored = [p for p in pairs if p["top"] is not None]
    tops = sorted(p["top"] for p in scored)

    lessons = await store.get_all_lessons()
    # Derived from the retrieve events themselves, not from lesson_stats: the
    # two disagree on 24 lessons, and "did a query ever return this" is a fact
    # about the events, not about a counter that another writer also touches.
    ever_returned = {lid for p in pairs for lid in p["lesson_ids"]}
    dead = [le.id for le in lessons if le.id not in ever_returned]

    slots_matched = sum(p["matched"] for p in pairs)
    slots_bridged = sum(p["bridged"] for p in pairs)
    slots = slots_matched + slots_bridged

    return {
        "corpus": {
            "lessons": len(lessons),
            "never_retrieved": len(dead),
            "never_retrieved_ids": dead[:60],
            "workflows": len(await store.get_all_workflows()),
        },
        "retrieval": {
            "queries": len(pairs),
            "slots": slots,
            "slots_matched": slots_matched,
            "slots_bridged": slots_bridged,
            "bridged_share": (slots_bridged / slots) if slots else 0.0,
            "top1_median": tops[len(tops) // 2] if tops else None,
            "top1_mean": (sum(tops) / len(tops)) if tops else None,
            "misses": sum(1 for p in scored if p["top"] < miss_threshold),
            "miss_threshold": miss_threshold,
            "zero_result_queries": sum(1 for p in pairs if not p["lesson_ids"]),
        },
        "concentration": _query_concentration(pairs),
    }


def _query_concentration(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """How many different questions are behind the query count.

    Every figure in `retrieval` counts events, and the hooks issue some of
    those events themselves. The git gate mandates `query_lessons('git
    commit')`, so that one string accounts for 521 of 1,161 recorded queries
    in the operator's own store, and one lesson wins 517 of them at a nearly
    constant score. Reported as a raw count, that reads as nine months of
    varied recall. It is one reflex firing.

    So every count here has a per-distinct-question twin, which weights each
    different question once however often it was asked. Neither number is the
    true one: the repeated query really is asked, and really does matter each
    time, which is what the raw figures measure. The pair is the point. A gap
    between them is the share of the evidence that the system generated for
    itself, and it cannot be read off a single number.
    """
    by_text: dict[str, list[float]] = {}
    for p in pairs:
        by_text.setdefault(p["query"], []).append(p["top"])

    counts = sorted(((t, len(v)) for t, v in by_text.items()), key=lambda r: -r[1])
    total = len(pairs)

    # One vote per distinct question: average that question's best score over
    # however many times it was asked, then aggregate those averages.
    per_question = sorted(
        sum(s for s in v if s is not None) / len([s for s in v if s is not None])
        for v in by_text.values()
        if any(s is not None for s in v)
    )

    return {
        "queries": total,
        "distinct_queries": len(by_text),
        "asked_once": sum(1 for _, n in counts if n == 1),
        "repeat_share": (
            sum(n for _, n in counts if n > 1) / total if total else 0.0
        ),
        "top_query_share": (counts[0][1] / total) if counts and total else 0.0,
        "most_repeated": [
            {"query": t, "count": n, "share": n / total}
            for t, n in counts[:5]
            if n > 1
        ],
        "top1_median_per_question": (
            per_question[len(per_question) // 2] if per_question else None
        ),
        "top1_mean_per_question": (
            sum(per_question) / len(per_question) if per_question else None
        ),
        "scored_questions": len(per_question),
    }


@app.get("/api/retrieval/timeseries")
async def get_retrieval_timeseries(bucket: str = "month") -> list[dict[str, Any]]:
    """Matched vs bridged volume and match quality per period.

    Two measures of different scale never share an axis, so the caller gets
    them as separate series to plot separately.
    """
    await ensure_initialized()
    width = 7 if bucket == "month" else 10  # YYYY-MM or YYYY-MM-DD
    buckets: dict[str, dict[str, Any]] = {}
    for p in await _retrieval_pairs():
        key = (p["ts"] or "")[:width]
        if not key:
            continue
        b = buckets.setdefault(key, {"period": key, "queries": 0, "matched": 0,
                                     "bridged": 0, "_tops": [], "misses": 0})
        b["queries"] += 1
        b["matched"] += p["matched"]
        b["bridged"] += p["bridged"]
        if p["top"] is not None:
            b["_tops"].append(p["top"])
            if p["top"] < DEFAULT_MISS_THRESHOLD:
                b["misses"] += 1
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        tops = b.pop("_tops")
        b["top1_mean"] = (sum(tops) / len(tops)) if tops else None
        b["scored_queries"] = len(tops)
        out.append(b)
    return out


@app.get("/api/retrieval/misses")
async def get_retrieval_misses(
    threshold: float = DEFAULT_MISS_THRESHOLD, limit: int = 100
) -> list[dict[str, Any]]:
    """Queries whose best MATCHED result scored below the threshold.

    The threshold is a choice, not a fact: BGE cosine scores cluster high, so
    the caller sets it and the UI labels it as chosen.
    """
    await ensure_initialized()
    misses = [
        {
            "ts": p["ts"],
            "query": p["query"],
            "top": p["top"],
            "returned": p["lesson_ids"][:5],
            "bridged": p["bridged"],
        }
        for p in await _retrieval_pairs()
        if p["top"] is not None and p["top"] < threshold
    ]
    misses.sort(key=lambda m: m["top"])
    return misses[:limit]


# NOT /api/lessons/effectiveness: `/api/lessons/{lesson_id}` is registered
# earlier and would capture it, returning null for a lesson called
# "effectiveness" instead of 404ing or matching here.
@app.get("/api/effectiveness")
async def get_lesson_effectiveness() -> list[dict[str, Any]]:
    """Per lesson: how often it was matched, how well, and how often appended.

    `mean_matched_score` deliberately excludes bridged slots. Including them is
    what made `mgcp-save-on-shutdown` read as 0.023 average relevance when it
    scored 0.510 every time it was genuinely matched.
    """
    await ensure_initialized()
    matched: dict[str, list[float]] = {}
    bridged: dict[str, int] = {}
    for p in await _retrieval_pairs():
        for i, score in enumerate(p["scores"]):
            if i >= len(p["lesson_ids"]):
                break
            lid = p["lesson_ids"][i]
            if score == BRIDGED_SENTINEL:
                bridged[lid] = bridged.get(lid, 0) + 1
            else:
                matched.setdefault(lid, []).append(score)

    out = []
    for lesson in await store.get_all_lessons():
        scores = matched.get(lesson.id, [])
        out.append({
            "id": lesson.id,
            "tags": lesson.tags,
            "trigger": lesson.trigger[:140],
            "usage_count": lesson.usage_count,
            "matched": len(scores),
            "bridged": bridged.get(lesson.id, 0),
            "mean_matched_score": (sum(scores) / len(scores)) if scores else None,
            "best_score": max(scores) if scores else None,
            "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
            "last_refined": lesson.last_refined.isoformat() if lesson.last_refined else None,
        })
    out.sort(key=lambda r: -(r["matched"] + r["bridged"]))
    return out


@app.get("/api/gate-audit")
async def get_gate_audit(limit: int = 400) -> dict[str, Any]:
    """Enforcement activity: what fired, what it cost, what was contested."""
    await ensure_initialized()
    rows = _read_gate_audit()
    denials = [r for r in rows if r.get("event") == "deny"]
    complies = [r for r in rows if r.get("event") == "comply"]
    adjudications = [r for r in rows if r.get("event") == "adjudication"]

    rule_fires: dict[str, int] = {}
    for r in denials:
        for name in (r.get("rules") or []):
            rule_fires[name] = rule_fires.get(name, 0) + 1

    per_day: dict[str, dict[str, int]] = {}
    for r in rows:
        day = (r.get("ts") or "")[:10]
        if not day:
            continue
        slot = per_day.setdefault(day, {"day": day, "deny": 0, "comply": 0,
                                        "adjudication": 0, "human_bypass": 0})
        event = r.get("event")
        if event in slot:
            slot[event] += 1

    tools: dict[str, int] = {}
    for r in denials:
        tools[r.get("tool_denied") or "unknown"] = tools.get(r.get("tool_denied") or "unknown", 0) + 1

    return {
        "totals": {
            "entries": len(rows),
            "deny": len(denials),
            "comply": len(complies),
            "adjudication": len(adjudications),
            "human_bypass": sum(1 for r in rows if r.get("event") == "human_bypass"),
            "apology_denials": sum(1 for r in denials if r.get("gate") == "apology"),
            "rule_denials": sum(1 for r in denials if r.get("gate") == "rules"),
            "sessions_affected": len({r.get("session_id") for r in denials if r.get("session_id")}),
        },
        "rule_fires": [{"rule": k, "fires": v}
                       for k, v in sorted(rule_fires.items(), key=lambda kv: -kv[1])],
        "tools_denied": [{"tool": k, "denials": v}
                         for k, v in sorted(tools.items(), key=lambda kv: -kv[1])],
        "per_day": [per_day[d] for d in sorted(per_day)],
        "adjudications": [
            {"ts": r.get("ts"), "verdict": r.get("verdict"),
             "flagged": (r.get("flagged_sentence") or "")[:400],
             "reasoning": (r.get("reasoning") or "")[:600]}
            for r in adjudications[-limit:]
        ],
    }


@app.get("/api/enforcement/rules")
async def get_enforcement_rules_api() -> list[dict[str, Any]]:
    """Live rules, each with how often it has actually denied something.

    A rule that has never fired is either dead or mis-triggered, and the audit
    log alone cannot tell you which — so the count is shown, not judged.
    """
    await ensure_initialized()
    from .enforcement import load_config

    fires: dict[str, int] = {}
    for r in _read_gate_audit():
        if r.get("event") != "deny":
            continue
        for name in (r.get("rules") or []):
            fires[name] = fires.get(name, 0) + 1
    try:
        config = load_config()
    except (json.JSONDecodeError, ValueError) as exc:
        return [{"error": f"enforcement_rules.json does not parse: {exc}"}]

    return [
        {
            "name": rule.name,
            "enabled": rule.enabled,
            "bypass_scope": rule.bypass_scope,
            "deny_reason": rule.deny_reason,
            "trigger": rule.trigger.model_dump(),
            "preconditions": [p.model_dump() for p in rule.preconditions],
            "fires": fires.get(rule.name, 0),
        }
        for rule in config.rules
    ]


@app.get("/api/rem/state")
async def get_rem_state_api() -> list[dict[str, Any]]:
    """Every project's REM schedule, with what is overdue.

    The cadence is per project; the corpus it maintains is global. Both halves
    matter, so the project's own session_count is reported beside each row.
    """
    await ensure_initialized()
    from .rem_config import DEFAULT_SCHEDULES

    out = []
    for project in await store.get_all_project_contexts():
        states = {s["operation"]: s for s in await store.get_rem_state(project.project_id)}
        for operation, schedule in DEFAULT_SCHEDULES.items():
            state = states.get(operation)
            next_due = state.get("next_due_session") if state else None
            out.append({
                "project_id": project.project_id,
                "project": project.project_name,
                "session_count": project.session_count,
                "operation": operation,
                "strategy": getattr(schedule, "strategy", None) or str(schedule),
                "last_run_session": (state or {}).get("last_run_session"),
                "next_due_session": next_due,
                "overdue": bool(next_due and project.session_count >= next_due),
                "never_run": state is None,
            })
    return out


@app.get("/api/soliloquies")
async def get_soliloquies_api(limit: int = 40) -> list[dict[str, Any]]:
    """The journal, newest first. Currently invisible in every other surface."""
    await ensure_initialized()
    # read_soliloquies returns (entry, project_id) pairs: the journal is stored
    # globally but read project-aware, so the caller can mark which entries came
    # from somewhere else. Entries written before tagging existed have no id.
    pairs = await store.read_soliloquies(limit=limit)
    projects = {p.project_id: p.project_name for p in await store.get_all_project_contexts()}
    return [
        {
            "id": entry.id,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "session_number": entry.session_number,
            "project_id": project_id,
            "project": projects.get(project_id) if project_id else None,
            "mood": entry.mood,
            "content": entry.content,
        }
        for entry, project_id in pairs
    ]


# Mount static files for other assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """Run the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    """Entry point for web server."""
    run_server()


if __name__ == "__main__":
    main()
