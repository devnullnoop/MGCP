# LLM Lesson Memory System via MCP

## Project Overview

A persistent, queryable memory system for LLM interactions that stores learned lessons in a graph structure. Instead of loading all context upfront (crushing token counts) or losing lessons to compression, the LLM queries for relevant lessons based on current task context.

### Core Problem Solved

- Lessons learned in one session are lost when context compresses
- Static CLAUDE.md files don't adapt or grow
- Loading all historical context is token-prohibitive
- No cross-project learning accumulation

### Solution

A Model Context Protocol (MCP) server that exposes a lesson graph. The LLM introspects and queries for relevant lessons dynamically, keeping context minimal while maintaining access to accumulated knowledge.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Claude / LLM                            │
└─────────────────────┬───────────────────────────────────────┘
                      │ MCP Protocol
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  MCP Lesson Server                          │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐  │
│  │ Query Handler  │  │ Lesson Manager │  │ Graph Walker  │  │
│  └────────────────┘  └────────────────┘  └───────────────┘  │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
┌───────────────────┐    ┌─────────────────────┐
│   Graph Store     │    │   Vector Store      │
│   (NetworkX/      │    │   (ChromaDB/        │
│    Neo4j/SQLite)  │    │    FAISS/Pinecone)  │
└───────────────────┘    └─────────────────────┘
```

---

## Data Model

### Lesson Node

```python
@dataclass
class Lesson:
    id: str                          # Unique identifier
    trigger: str                     # When this lesson applies (regex/keywords)
    action: str                      # What to do (imperative)
    rationale: str | None            # Why (optional, for complex lessons)
    examples: list[Example]          # Good/bad examples
    version: int                     # Refinement count
    created_at: datetime
    last_refined: datetime
    tags: list[str]                  # For categorization
    parent_id: str | None            # Hierarchy (global → specific)
    related_ids: list[str]           # Cross-links to related lessons

@dataclass
class Example:
    label: str                       # "good" or "bad"
    code: str                        # The example
    explanation: str | None          # Why it's good/bad
```

### Example Lesson

```python
Lesson(
    id="rust-memory-estimation",
    trigger="estimate|memory|calculation|formula",
    action="Test with known inputs before integrating",
    rationale="Formulas that look correct can be wildly wrong (e.g., ignoring structural sharing)",
    examples=[
        Example(
            label="bad",
            code="estimated_nodes = prefix_count * avg_length * 500",
            explanation="Doesn't account for trie path sharing"
        ),
        Example(
            label="good",
            code="build small sample, measure actual, extrapolate",
            explanation="Empirical verification catches formula errors"
        )
    ],
    version=1,
    tags=["rust", "estimation", "verification"],
    parent_id="global-verify-calculations",
    related_ids=["global-sanity-check", "rust-testing"]
)
```

### Graph Structure

```
global-verify-before-assert (ROOT)
├── global-research-apis
│   ├── rust-check-crate-versions
│   ├── python-check-pypi-versions
│   └── js-check-npm-versions
├── global-verify-calculations
│   ├── rust-memory-estimation
│   ├── python-numpy-broadcasting
│   └── global-sanity-check-magnitude
└── global-test-before-integrate
    ├── rust-test-with-known-inputs
    └── python-test-with-known-inputs
```

---

## MCP Server Interface

### Tools Exposed to LLM

```python
# Discovery
def list_lesson_categories() -> list[str]:
    """Return top-level lesson categories"""

def get_lessons_by_category(category: str) -> list[LessonSummary]:
    """Get lessons in a category (summaries only, not full content)"""

# Retrieval
def get_lesson(lesson_id: str) -> Lesson:
    """Fetch full lesson by ID"""

def query_lessons(task_description: str, limit: int = 5) -> list[Lesson]:
    """Semantic search: find lessons relevant to current task"""

def get_related_lessons(lesson_id: str) -> list[LessonSummary]:
    """Get lessons linked to this one"""

def spider_lessons(lesson_id: str, depth: int = 2) -> list[Lesson]:
    """Walk the graph from a lesson, fetching connected lessons"""

# Refinement
def add_lesson(lesson: Lesson) -> str:
    """Add a new lesson, return its ID"""

def refine_lesson(lesson_id: str, refinement: str) -> Lesson:
    """Update a lesson with new insight, increment version"""

def link_lessons(lesson_id_a: str, lesson_id_b: str) -> None:
    """Create relationship between lessons"""
```

### Example MCP Interaction Flow

```
LLM receives task: "Implement memory estimation for a cache"

1. LLM calls: query_lessons("memory estimation calculation")
   → Returns: [rust-memory-estimation, global-verify-calculations]

2. LLM calls: spider_lessons("rust-memory-estimation", depth=1)
   → Returns: [global-verify-calculations, global-sanity-check-magnitude]

3. LLM now has relevant lessons in context:
   - Test with known inputs
   - Watch for structural sharing assumptions
   - Sanity check output magnitude

4. LLM implements with verification built-in

5. If new insight gained, LLM calls:
   refine_lesson("rust-memory-estimation", "Also verify units match (bytes vs KB vs MB)")
```

---

## Technology Stack (Python)

### Core Framework

| Component | Options | Recommendation |
|-----------|---------|----------------|
| MCP Server | `mcp` (official SDK) | Official Python MCP SDK |
| Graph Store | NetworkX, Neo4j, SQLite | Start with NetworkX, migrate to Neo4j if needed |
| Vector Store | ChromaDB, FAISS, Pinecone | ChromaDB (simple, local, good enough) |
| Embeddings | sentence-transformers, OpenAI | sentence-transformers (local, free) |
| Persistence | SQLite, JSON files | SQLite for structured + JSON for export |

### Dependencies

```toml
[project]
dependencies = [
    "mcp",                    # MCP server SDK
    "networkx",               # Graph operations
    "chromadb",               # Vector storage
    "sentence-transformers",  # Local embeddings
    "pydantic",               # Data validation
    "sqlalchemy",             # Persistence layer
]
```

---

## Implementation Phases

### Phase 1: Minimal Viable Memory

**Goal**: Basic lesson storage and retrieval via MCP

- [ ] Set up MCP server skeleton in Python
- [ ] Implement Lesson dataclass with Pydantic
- [ ] In-memory graph using NetworkX
- [ ] Basic tools: `add_lesson`, `get_lesson`, `list_lessons`
- [ ] Persist to JSON on shutdown, load on startup
- [ ] Connect to Claude Code and verify it works

**Deliverable**: Can add lessons and retrieve them by ID

### Phase 2: Semantic Search

**Goal**: Query lessons by task context, not just ID

- [ ] Integrate sentence-transformers for embeddings
- [ ] ChromaDB for vector storage
- [ ] Implement `query_lessons(task_description)`
- [ ] Embed lesson triggers + actions for similarity search
- [ ] Test retrieval quality with sample queries

**Deliverable**: LLM can ask "what do I know about API usage?" and get relevant lessons

### Phase 3: Graph Traversal

**Goal**: Spider related lessons, hierarchical structure

- [ ] Implement parent/child relationships
- [ ] Implement `related_ids` cross-links
- [ ] `spider_lessons` to walk graph from starting node
- [ ] `get_related_lessons` for discovering connections
- [ ] Category-based browsing

**Deliverable**: LLM can explore lesson graph, pull chains of related knowledge

### Phase 4: Refinement & Learning

**Goal**: Lessons improve over time

- [ ] `refine_lesson` with version tracking
- [ ] Merge duplicate lessons
- [ ] Prune stale lessons (unused for N days)
- [ ] Analytics: which lessons get used most
- [ ] Export/import for backup and sharing

**Deliverable**: Living knowledge base that sharpens with use

### Phase 5: Intelligence Layer

**Goal**: Proactive lesson suggestion

- [ ] Task classifier: detect task type from initial prompt
- [ ] Auto-load relevant category on session start
- [ ] Suggest lesson creation when LLM makes verified mistake
- [ ] Cross-project lesson sharing (global vs project-specific stores)

**Deliverable**: System that anticipates needed context

---

## File Structure

```
llm-lesson-memory/
├── pyproject.toml
├── README.md
├── src/
│   └── lesson_memory/
│       ├── __init__.py
│       ├── server.py           # MCP server entry point
│       ├── models.py           # Pydantic models (Lesson, Example)
│       ├── graph.py            # NetworkX graph operations
│       ├── vector_store.py     # ChromaDB integration
│       ├── embeddings.py       # sentence-transformers wrapper
│       ├── persistence.py      # SQLite/JSON storage
│       └── tools.py            # MCP tool definitions
├── data/
│   ├── lessons.db              # SQLite database
│   └── chroma/                 # ChromaDB storage
└── tests/
    ├── test_graph.py
    ├── test_retrieval.py
    └── test_mcp_tools.py
```

---

## Configuration

```yaml
# config.yaml
server:
  name: "lesson-memory"
  version: "0.1.0"

storage:
  type: "sqlite"
  path: "~/.llm-lessons/lessons.db"

vector:
  type: "chromadb"
  path: "~/.llm-lessons/chroma"

embeddings:
  model: "all-MiniLM-L6-v2"  # Fast, good quality
  # model: "all-mpnet-base-v2"  # Better quality, slower

graph:
  backend: "networkx"  # or "neo4j"

# Global lessons always loaded at session start
bootstrap_lessons:
  - "global-verify-before-assert"
  - "global-research-apis"
```

---

## Claude Code Integration

Once the MCP server is running, add to Claude Code settings:

```json
{
  "mcpServers": {
    "lesson-memory": {
      "command": "python",
      "args": ["-m", "lesson_memory.server"],
      "env": {
        "LESSON_DB_PATH": "~/.llm-lessons/lessons.db"
      }
    }
  }
}
```

The LLM will then have access to lesson memory tools in every session.

---

## Success Metrics

1. **Context efficiency**: Relevant lessons loaded in <500 tokens average
2. **Retrieval quality**: >80% of retrieved lessons rated "relevant" by user
3. **Learning accumulation**: Lessons grow and refine over weeks of use
4. **Error reduction**: Repeated mistakes decrease as lessons accumulate
5. **Cross-session continuity**: Lessons from project A apply correctly in project B

---

## Open Questions

1. **Conflict resolution**: What if two lessons contradict? Priority by recency? By specificity?
2. **Lesson decay**: Should unused lessons fade or stay forever?
3. **User override**: How does user correct a wrong lesson?
4. **Multi-user**: Shared team lessons vs personal lessons?
5. **Lesson provenance**: Track which conversation/mistake spawned each lesson?

---

## Next Steps

1. Review this document, refine scope
2. Set up Python project structure
3. Implement Phase 1 (minimal MCP server with basic storage)
4. Test integration with Claude Code
5. Iterate based on real usage
