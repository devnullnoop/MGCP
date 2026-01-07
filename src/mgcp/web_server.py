"""MGCP web server for telemetry visualization and REST API."""

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .graph import LessonGraph
from .models import ProjectContext, ProjectTodo
from .persistence import LessonStore
from .telemetry import TelemetryLogger
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

# Global instances
telemetry: TelemetryLogger | None = None
store: LessonStore | None = None
vector_store: VectorStore | None = None
catalogue_vector = None  # CatalogueVectorStore, initialized lazily
graph: LessonGraph | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize stores on startup."""
    global telemetry, store, vector_store, graph

    telemetry = TelemetryLogger()
    store = LessonStore()
    vector_store = VectorStore()
    graph = LessonGraph()

    # Load existing lessons into graph
    lessons = await store.get_all_lessons()
    for lesson in lessons:
        graph.add_lesson(lesson)

    logger.info(f"Web server initialized with {len(lessons)} lessons")
    yield

    logger.info("Shutting down web server")


app = FastAPI(
    title="MGCP Telemetry API",
    description="REST API and WebSocket for MGCP visualization",
    version="1.0.0",
    lifespan=lifespan,
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
        vector_store = VectorStore()
        graph = LessonGraph()
        lessons = await store.get_all_lessons()
        for lesson in lessons:
            graph.add_lesson(lesson)


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
        "vector_store": "connected" if vector_store else "not initialized",
        "catalogue_store": "connected" if catalogue_vector else "not initialized",
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

        # Add legacy related links (for backwards compatibility)
        existing_targets = {r.target for r in lesson.relationships}
        for related_id in lesson.related_ids:
            if related_id not in existing_targets:
                links.append({
                    "source": lesson.id,
                    "target": related_id,
                    "relation": "related",
                    "weight": 0.5,
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

    return {"nodes": nodes, "links": links}


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
    conn = await store._get_conn()
    try:
        await conn.execute("DELETE FROM project_contexts WHERE project_id = ?", (project_id,))
        await conn.commit()
    finally:
        await conn.close()

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

    # Broadcast update via WebSocket
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

    # Increment version on edit
    lesson.version += 1
    from datetime import UTC, datetime
    lesson.last_refined = datetime.now(UTC)

    await store.update_lesson(lesson)

    # Re-index in vector store
    vector_store.add_lesson(lesson)

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
        vector_store.remove_lesson(lesson_id)
        graph.remove_lesson(lesson_id)

    return {"deleted": lesson_id}


# ============================================================================
# WebSocket for Real-time Updates
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """Real-time event stream via WebSocket."""
    await manager.connect(websocket)

    if not telemetry:
        await websocket.close()
        return

    # Subscribe to telemetry events
    queue = telemetry.subscribe()

    try:
        while True:
            # Wait for events from telemetry
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json({
                    "id": event.id,
                    "timestamp": event.timestamp.isoformat(),
                    "type": event.event_type.value,
                    "session_id": event.session_id,
                    "payload": event.payload,
                })
            except TimeoutError:
                # Send heartbeat to keep connection alive
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        telemetry.unsubscribe(queue)
        manager.disconnect(websocket)


# ============================================================================
# Static Files & Dashboard
# ============================================================================

# Get the docs directory path
DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


@app.get("/")
async def serve_dashboard():
    """Serve the main dashboard."""
    dashboard_path = DOCS_DIR / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return HTMLResponse("<h1>Dashboard not found. Run the setup script.</h1>")


@app.get("/visualizer")
async def serve_visualizer():
    """Serve the memory visualizer."""
    viz_path = DOCS_DIR / "memory-visualizer.html"
    if viz_path.exists():
        return FileResponse(viz_path)
    return HTMLResponse("<h1>Visualizer not found.</h1>")


@app.get("/session")
async def serve_session_detail():
    """Serve the session detail page."""
    session_path = DOCS_DIR / "session-detail.html"
    if session_path.exists():
        return FileResponse(session_path)
    return HTMLResponse("<h1>Session detail page not found.</h1>")


@app.get("/query-tree")
async def serve_query_tree():
    """Serve the query decision tree visualization."""
    tree_path = DOCS_DIR / "query-tree.html"
    if tree_path.exists():
        return FileResponse(tree_path)
    return HTMLResponse("<h1>Query tree visualization not found.</h1>")


@app.get("/projects")
async def serve_projects():
    """Serve the project contexts management page."""
    projects_path = DOCS_DIR / "projects.html"
    if projects_path.exists():
        return FileResponse(projects_path)
    return HTMLResponse("<h1>Projects page not found.</h1>")


@app.get("/lessons")
async def serve_lessons():
    """Serve the lessons management page."""
    lessons_path = DOCS_DIR / "lessons.html"
    if lessons_path.exists():
        return FileResponse(lessons_path)
    return HTMLResponse("<h1>Lessons page not found.</h1>")


# Mount static files for other assets
if DOCS_DIR.exists():
    app.mount("/docs", StaticFiles(directory=str(DOCS_DIR)), name="docs")


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """Run the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    """Entry point for web server."""
    run_server()


if __name__ == "__main__":
    main()
