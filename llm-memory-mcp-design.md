# MGCP - Memory Graph Core Primitives

## Project Overview

A persistent, queryable memory system for LLM interactions that stores learned lessons in a graph structure. Instead of loading all context upfront (crushing token counts) or losing lessons to compression, the LLM queries for relevant lessons based on current task context.

> **Status**: v1.2.0 - Alpha/Research project. Phases 1-5 complete, actively dogfooding.

### Core Problem Solved

- Lessons learned in one session are lost when context compresses
- Static CLAUDE.md files don't adapt or grow
- Loading all historical context is token-prohibitive
- No cross-project learning accumulation

### Solution

A Model Context Protocol (MCP) server that exposes a lesson graph plus project context. The LLM introspects and queries for relevant lessons dynamically, keeping context minimal while maintaining access to accumulated knowledge.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Claude / LLM                            │
└─────────────────────┬───────────────────────────────────────┘
                      │ MCP Protocol
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    MGCP Server                              │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐  │
│  │ Query Handler  │  │ Lesson Manager │  │ Graph Walker  │  │
│  └────────────────┘  └────────────────┘  └───────────────┘  │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐  │
│  │Project Context │  │    Catalogue   │  │   Workflows   │  │
│  └────────────────┘  └────────────────┘  └───────────────┘  │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌───────────────┐ ┌─────────────┐ ┌─────────────────┐
│  Graph Store  │ │Vector Store │ │ SQLite/JSON     │
│  (NetworkX)   │ │ (ChromaDB)  │ │ (Persistence)   │
└───────────────┘ └─────────────┘ └─────────────────┘
```

---

## Data Model

### Lesson Node

```python
class Lesson(BaseModel):
    id: str                                    # Unique identifier (kebab-case)
    trigger: str                               # When this applies (keywords/patterns)
    action: str                                # What to do (imperative)
    rationale: str = ""                        # Why (optional)
    examples: list[Example] = []               # Good/bad examples
    version: int = 1                           # Refinement count
    created_at: datetime
    last_refined: datetime | None
    tags: list[str] = []                       # For categorization
    parent_id: str | None = None               # Hierarchy (deprecated)
    relationships: list[LessonRelationship]    # Typed cross-links

class LessonRelationship(BaseModel):
    target_id: str                             # Related lesson ID
    relationship_type: str                     # prerequisite, alternative, complements, etc.
    weight: float = 0.5                        # Strength (0-1)
    context: list[str] = []                    # When this applies

class Example(BaseModel):
    label: str                                 # "good" or "bad"
    code: str                                  # The example
    explanation: str | None = None             # Why it's good/bad
```

### Project Context

```python
class ProjectContext(BaseModel):
    project_path: str                          # Absolute path (unique key)
    project_name: str                          # Human-readable name
    todos: list[ProjectTodo] = []              # Persistent todos
    active_files: list[str] = []               # Files being worked on
    recent_decisions: list[str] = []           # Decision history
    notes: str = ""                            # Freeform notes
    session_count: int = 0                     # Number of sessions
    last_accessed: datetime

class ProjectTodo(BaseModel):
    content: str                               # What needs to be done
    status: str = "pending"                    # pending, in_progress, completed, blocked
    priority: int = 0                          # Higher = more urgent
    notes: str = ""                            # Additional context
```

### Project Catalogue

```python
class ProjectCatalogue(BaseModel):
    project_path: str
    arch_notes: list[ArchNote] = []            # Architecture notes, gotchas
    security_notes: list[SecurityNote] = []   # Security concerns
    conventions: list[Convention] = []         # Coding conventions
    couplings: list[FileCoupling] = []         # Files that change together
    decisions: list[Decision] = []             # Architectural decisions
    error_patterns: list[ErrorPattern] = []    # Common errors and solutions
    dependencies: list[Dependency] = []        # Frameworks, libraries, tools
    custom_items: list[GenericItem] = []       # User-defined types
```

### Example Lesson

```python
Lesson(
    id="verify-api-versions",
    trigger="API, library, version, documentation, example",
    action="Always check current API/library versions before using examples from docs or Stack Overflow",
    rationale="Online examples may be outdated. APIs change between versions.",
    examples=[
        Example(
            label="bad",
            code="# Copy-pasted from 2019 Stack Overflow\nrequests.get(url, verify=False)",
            explanation="Outdated pattern, security risk"
        ),
        Example(
            label="good",
            code="# First: pip show requests -> version\n# Then: check requests docs for current best practice",
            explanation="Verify version, then check current docs"
        )
    ],
    version=2,
    tags=["api", "verification", "dependencies"],
    relationships=[
        LessonRelationship(target_id="check-breaking-changes", relationship_type="related"),
        LessonRelationship(target_id="read-changelogs", relationship_type="complements")
    ]
)
```

---

## MCP Server Interface

### Tools Exposed to LLM (23+ total)

#### Lesson Discovery & Retrieval
```python
def query_lessons(task_description: str, limit: int = 5) -> list[Lesson]:
    """Semantic search: find lessons relevant to current task"""

def get_lesson(lesson_id: str) -> Lesson:
    """Fetch full lesson by ID"""

def spider_lessons(lesson_id: str, depth: int = 2) -> list[Lesson]:
    """Walk the graph from a lesson, fetching connected lessons"""

def list_categories() -> list[str]:
    """Return top-level lesson categories (parent lessons)"""

def get_lessons_by_category(category_id: str) -> list[LessonSummary]:
    """Get lessons under a category"""
```

#### Lesson Management
```python
def add_lesson(id, trigger, action, rationale, tags, parent_id) -> str:
    """Add a new lesson"""

def refine_lesson(lesson_id: str, refinement: str, new_action: str = "") -> Lesson:
    """Update a lesson with new insight, increment version"""

def link_lessons(lesson_id_a, lesson_id_b, relationship_type, weight, context) -> None:
    """Create typed relationship between lessons"""
```

#### Project Context
```python
def get_project_context(project_path: str) -> ProjectContext:
    """Load saved context for a project"""

def save_project_context(project_path, notes, active_files, decision) -> None:
    """Persist context for next session"""

def add_project_todo(project_path, todo, priority, notes) -> None:
    """Add a todo item"""

def update_project_todo(project_path, todo_index, status, notes) -> None:
    """Update todo status"""

def list_projects() -> list[ProjectSummary]:
    """List all tracked projects"""
```

#### Project Catalogue
```python
def search_catalogue(query, project_path, item_types, limit) -> list[CatalogueItem]:
    """Semantic search across catalogue items"""

def add_catalogue_arch_note(project_path, title, description, category) -> None:
def add_catalogue_security_note(project_path, title, description, severity) -> None:
def add_catalogue_dependency(project_path, name, purpose, dep_type, version) -> None:
def add_catalogue_convention(project_path, title, rule, category) -> None:
def add_catalogue_coupling(project_path, files, reason, direction) -> None:
def add_catalogue_decision(project_path, title, decision, rationale, alternatives) -> None:
def add_catalogue_error_pattern(project_path, error_signature, cause, solution) -> None:
def add_catalogue_custom_item(project_path, item_type, title, content) -> None:
def remove_catalogue_item(project_path, item_type, identifier) -> None:
def get_catalogue_item(project_path, item_type, identifier) -> CatalogueItem:
```

#### Workflows
```python
def list_workflows() -> list[Workflow]:
    """List available development workflows"""

def get_workflow(workflow_id: str) -> Workflow:
    """Get workflow with steps and linked lessons"""

def get_workflow_step(workflow_id, step_id, expand_lessons) -> WorkflowStep:
    """Get step details with linked lessons"""
```

---

## Technology Stack

| Component | Choice | Notes |
|-----------|--------|-------|
| MCP Server | FastMCP | Official Python MCP SDK wrapper |
| Graph Store | NetworkX | In-memory, persisted to JSON |
| Vector Store | ChromaDB | Local, embedded |
| Embeddings | sentence-transformers | `all-MiniLM-L6-v2` model |
| Persistence | aiosqlite + JSON | Async SQLite for structured data |
| Validation | Pydantic | Data models and serialization |
| Web UI | FastAPI + WebSockets | Dashboard for visualization |

### Dependencies

```toml
[project]
dependencies = [
    "mcp>=1.0.0",
    "fastmcp>=0.1.0",
    "networkx>=3.0",
    "chromadb>=0.4.0",
    "sentence-transformers>=2.2.0",
    "pydantic>=2.0.0",
    "aiosqlite>=0.19.0",
    "fastapi>=0.100.0",
    "uvicorn>=0.23.0",
]
```

---

## Implementation Phases

### Phase 1: Minimal Viable Memory ✅ Complete

- [x] Set up MCP server skeleton with FastMCP
- [x] Implement Lesson dataclass with Pydantic
- [x] In-memory graph using NetworkX
- [x] Basic tools: `add_lesson`, `get_lesson`, `query_lessons`
- [x] SQLite persistence with JSON backup
- [x] Connect to Claude Code and verify it works

### Phase 2: Semantic Search ✅ Complete

- [x] Integrate sentence-transformers for embeddings
- [x] ChromaDB for vector storage
- [x] Implement `query_lessons(task_description)`
- [x] Embed lesson triggers + actions for similarity search
- [x] Relevance scoring and ranking

### Phase 3: Graph Traversal ✅ Complete

- [x] Implement parent/child relationships
- [x] Typed relationships with `LessonRelationship`
- [x] `spider_lessons` to walk graph from starting node
- [x] `get_related_lessons` for discovering connections
- [x] Category-based browsing

### Phase 4: Refinement & Learning ✅ Complete

- [x] `refine_lesson` with version tracking
- [x] Typed relationship system (prerequisite, alternative, complements, etc.)
- [x] Project catalogue system (arch notes, security, conventions, etc.)
- [x] Usage telemetry and analytics
- [x] Web dashboard with graph visualization

### Phase 5: Quality of Life ✅ Complete

- [x] Multi-client support (8 LLM clients)
- [x] Export/import lessons (`mgcp-export`, `mgcp-import`)
- [x] Duplicate detection (`mgcp-duplicates`)
- [x] Backup and restore (`mgcp-backup`)
- [x] Proactive hooks (UserPromptSubmit for git/catalogue reminders)
- [x] Project deduplication with unique constraints

### Phase 6: Proactive Intelligence 🔮 Planned

- [ ] Auto-suggest lessons from conversations
- [ ] Feedback loop (track which lessons were helpful)
- [ ] Git integration (parse commits/PRs for learnings)
- [ ] Cross-project global lessons
- [ ] Lesson templates (Error→Solution, Gotcha→Workaround)
- [ ] Lesson quality scoring

---

## File Structure

```
MGCP/
├── pyproject.toml
├── README.md
├── CLAUDE.md                    # Instructions for Claude Code
├── CHANGELOG.md
├── CONTRIBUTING.md
├── src/
│   └── mgcp/
│       ├── __init__.py
│       ├── server.py            # MCP server (34 tools)
│       ├── models.py            # Pydantic models
│       ├── graph.py             # NetworkX graph operations
│       ├── vector_store.py      # ChromaDB for lessons
│       ├── catalogue_vector_store.py  # ChromaDB for catalogue
│       ├── persistence.py       # SQLite/JSON storage
│       ├── telemetry.py         # Usage tracking
│       ├── web_server.py        # FastAPI dashboard
│       ├── launcher.py          # Unified CLI
│       ├── bootstrap.py         # Initial lesson seeding
│       ├── migrations.py        # Database migrations
│       ├── init_project.py      # Multi-client setup + hooks
│       ├── data_ops.py          # Export, import, duplicates
│       └── backup.py            # Backup and restore
├── .claude/
│   ├── hooks/
│   │   ├── session-init.py      # SessionStart hook
│   │   ├── git-reminder.py      # UserPromptSubmit (git)
│   │   ├── catalogue-reminder.py # UserPromptSubmit (catalogue)
│   │   ├── mgcp-reminder.sh     # PostToolUse hook
│   │   └── mgcp-precompact.sh   # PreCompact hook
│   └── settings.json            # Hook configuration
├── docs/
│   ├── architecture.html        # Interactive diagram
│   └── *.html                   # Other visualizations
├── examples/
│   └── claude-hooks/            # Hook templates
└── tests/
    ├── test_basic.py
    ├── test_graph.py
    ├── test_retrieval.py
    └── ...                      # 245 tests total
```

---

## Configuration

MGCP uses environment variables and convention over configuration:

```bash
# Data directory (default: ~/.mgcp)
MGCP_DATA_DIR=~/.mgcp

# Database file (default: $MGCP_DATA_DIR/lessons.db)
MGCP_DB_PATH=~/.mgcp/lessons.db

# ChromaDB directory (default: $MGCP_DATA_DIR/chroma)
MGCP_CHROMA_DIR=~/.mgcp/chroma
```

Data is stored in `~/.mgcp/` by default:
- `lessons.db` - SQLite database (lessons, contexts, telemetry)
- `chroma/` - ChromaDB vector embeddings

---

## Claude Code Integration

### MCP Server Configuration

Add to Claude Code global settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "mgcp": {
      "command": "python",
      "args": ["-m", "mgcp.server"],
      "cwd": "/path/to/MGCP"
    }
  }
}
```

### Project Hooks

MGCP creates proactive hooks in each project's `.claude/` directory:

| Hook | Event | Purpose |
|------|-------|---------|
| `session-init.py` | SessionStart | Load context, inject instructions |
| `git-reminder.py` | UserPromptSubmit | Detect git keywords, remind to query lessons |
| `catalogue-reminder.py` | UserPromptSubmit | Detect library/decision mentions, remind to catalogue |
| `mgcp-reminder.sh` | PostToolUse | Remind to save lessons after edits |
| `mgcp-precompact.sh` | PreCompact | Critical reminder before context compression |

The `UserPromptSubmit` hooks make lessons **proactive** - they detect keywords in user messages and inject reminders before Claude acts.

### Setup Command

```bash
mgcp-init                          # Auto-detect and configure clients
mgcp-init --client claude-code     # Configure specific client
mgcp-init --verify                 # Verify setup works
```

---

## Example Interaction Flow

```
User: "Implement memory estimation for a cache"

1. SessionStart hook fires:
   → Injects instructions to call get_project_context and query_lessons

2. Claude calls: query_lessons("memory estimation calculation")
   → Returns: [verify-calculations, test-with-known-inputs]

3. Claude calls: spider_lessons("verify-calculations", depth=1)
   → Returns: [sanity-check-magnitude, empirical-verification]

4. Claude now has relevant lessons in context:
   - Test with known inputs before integrating
   - Watch for structural sharing assumptions
   - Sanity check output magnitude

5. Claude implements with verification built-in

6. Claude gains insight, calls:
   refine_lesson("verify-calculations", "Also verify units match (bytes vs KB)")

7. Before committing, git-reminder.py hook fires:
   → Injects reminder to query lessons for git workflow

8. Claude calls: query_lessons("git commit workflow")
   → Returns: [no-claude-attribution-in-commits, save-before-commit]

9. Claude follows lessons, creates clean commit
```

---

## Success Metrics

1. **Context efficiency**: Relevant lessons loaded in <500 tokens average
2. **Retrieval quality**: Semantic search finds relevant lessons >80% of time
3. **Learning accumulation**: 50+ lessons after weeks of use
4. **Session continuity**: Project context restores seamlessly
5. **Proactive firing**: Hooks surface relevant lessons automatically
6. **Error reduction**: Repeated mistakes decrease as lessons accumulate

---

## Resolved Design Questions

| Question | Resolution |
|----------|------------|
| Conflict resolution | Typed relationships + relevance scoring. More specific lessons rank higher. |
| Lesson decay | No automatic decay. Manual cleanup via `mgcp-duplicates` and delete. |
| User override | `refine_lesson` updates lesson, `remove_catalogue_item` deletes. |
| Multi-user | Currently single-user. Future: export/import for sharing. |
| Lesson provenance | `created_at`, `last_refined`, version tracking. No conversation linking yet. |
| Proactive suggestions | UserPromptSubmit hooks detect keywords and inject reminders. |

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `mgcp` | Start MCP server |
| `mgcp-init` | Configure LLM clients (8 supported) |
| `mgcp-init --verify` | Verify setup |
| `mgcp-init --doctor` | Diagnose issues |
| `mgcp-bootstrap` | Seed initial lessons |
| `mgcp-dashboard` | Start web UI (port 8765) |
| `mgcp-export lessons -o FILE` | Export lessons to JSON |
| `mgcp-import FILE` | Import lessons |
| `mgcp-duplicates` | Find similar lessons |
| `mgcp-backup` | Backup all data |
| `mgcp-backup --restore FILE` | Restore from backup |

---

## Next Steps

1. **Phase 6**: Implement proactive intelligence features
2. **Feedback loop**: Track which lessons are helpful
3. **Git integration**: Parse commits for learnings
4. **Lesson templates**: Structured formats for common patterns
5. **Cross-project globals**: Lessons that apply everywhere
