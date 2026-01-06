# MGCP - Memory Graph Control Protocol

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

**Persistent, graph-based memory for LLM interactions via the Model Context Protocol.**

MGCP solves a fundamental problem with LLM assistants: **they forget everything between sessions**. Every conversation starts from zero. Lessons learned, project context, architectural decisions—all lost.

MGCP gives your LLM a persistent memory that:
- **Remembers lessons** learned across all your projects
- **Stores project context** so sessions can resume seamlessly
- **Uses semantic search** to surface relevant knowledge at the right time
- **Builds a knowledge graph** of interconnected lessons

## The Problem

Without persistent memory, LLMs:
- Re-learn the same lessons in every session
- Lose track of project decisions and context
- Can't transfer knowledge between projects
- Require expensive full-context loading from static files

## The Solution

```
┌─────────────────┐     MCP Protocol      ┌─────────────────┐
│   Claude Code   │◄────────────────────►│   MGCP Server   │
│   or any LLM    │                       │                 │
└─────────────────┘                       └────────┬────────┘
                                                   │
                                    ┌──────────────┼──────────────┐
                                    │              │              │
                              ┌─────▼─────┐ ┌─────▼─────┐ ┌──────▼──────┐
                              │  Lesson   │ │  Vector   │ │   Project   │
                              │   Graph   │ │   Store   │ │   Context   │
                              └───────────┘ └───────────┘ └─────────────┘
```

MGCP provides **23 MCP tools** that let your LLM:
- Query relevant lessons semantically before starting any task
- Store new lessons as they're discovered
- Traverse related knowledge through the graph
- Save and restore project-specific context between sessions

## Quick Start

### 1. Install MGCP

```bash
git clone https://github.com/devnullnoop/MGCP.git
cd MGCP

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install MGCP
pip3 install -e ".[dev]"
```

> **Note**: Using a virtual environment is strongly recommended to avoid dependency conflicts with other packages (e.g., selenium's urllib3 requirements).

### 2. Configure Claude Code

Add to `~/.config/claude-code/settings.json`:

```json
{
  "mcpServers": {
    "mgcp": {
      "command": "/path/to/MGCP/.venv/bin/python3",
      "args": ["-m", "mgcp.server"],
      "cwd": "/path/to/MGCP"
    }
  }
}
```

> **Tip**: Using the full path to the venv python ensures MGCP runs with the correct dependencies regardless of your system's default Python.

### 3. Bootstrap Initial Lessons

```bash
mgcp-bootstrap
```

### 4. Start the Dashboard

```bash
mgcp-dashboard
# Opens http://127.0.0.1:8765
```

### 5. (Optional) Enable Auto-Loading

For automatic context loading at session start, copy the hook to your project:

```bash
mkdir -p your-project/.claude/hooks
cp .claude/hooks/session-init.py your-project/.claude/hooks/
cp examples/claude-hooks/settings.json your-project/.claude/
```

## Features

### Lesson Memory
- **Semantic Search**: Find relevant lessons using natural language queries
- **Graph Traversal**: Explore connected knowledge through typed relationships
- **Hierarchical Organization**: Parent/child lesson categories
- **Typed Relationships**: prerequisite, alternative, complements, specializes, etc.
- **Usage Tracking**: Lessons surface based on actual retrieval patterns

### Project Context
- **Session Continuity**: Pick up exactly where you left off
- **Todo Tracking**: Persistent todos that survive session boundaries
- **Decision History**: Remember architectural choices and their rationale
- **Active Files**: Track which files you're currently working on

### Project Catalogue
- **Architecture Notes**: Document patterns and gotchas
- **Security Tracking**: Track vulnerabilities with severity and status
- **Coding Conventions**: Store naming rules and style guides
- **File Couplings**: Know which files typically change together
- **Error Patterns**: Common errors and their solutions

## Commands

| Command | Description |
|---------|-------------|
| `mgcp` | Start MCP server (for Claude Code integration) |
| `mgcp-bootstrap` | Seed database with initial lessons |
| `mgcp-dashboard` | Start web dashboard on port 8765 |
| `mgcp-launcher status` | Show system status |
| `mgcp-launcher all` | Start both dashboard and MCP server |

## MCP Tools Reference

### Lesson Discovery & Retrieval
| Tool | Purpose |
|------|---------|
| `query_lessons` | Semantic search for relevant lessons |
| `get_lesson` | Get full lesson details by ID |
| `spider_lessons` | Traverse related lessons from a starting point |
| `list_categories` | Browse top-level lesson categories |
| `get_lessons_by_category` | Get lessons under a category |

### Lesson Management
| Tool | Purpose |
|------|---------|
| `add_lesson` | Create a new lesson |
| `refine_lesson` | Improve an existing lesson |
| `link_lessons` | Create typed relationships between lessons |

### Project Context
| Tool | Purpose |
|------|---------|
| `get_project_context` | Load saved context for a project |
| `save_project_context` | Persist context for next session |
| `add_project_todo` | Add a todo item |
| `update_project_todo` | Update todo status |
| `list_projects` | List all tracked projects |

### Project Catalogue
| Tool | Purpose |
|------|---------|
| `search_catalogue` | Semantic search across catalogue items |
| `add_catalogue_arch_note` | Add architecture note/gotcha |
| `add_catalogue_security_note` | Add security consideration |
| `add_catalogue_dependency` | Track framework/library/tool |
| `add_catalogue_convention` | Document coding conventions |
| `add_catalogue_coupling` | Record file dependencies |
| `add_catalogue_decision` | Document decisions with rationale |
| `add_catalogue_error_pattern` | Record error solutions |
| `remove_catalogue_item` | Remove a catalogue item |
| `get_catalogue_item` | Get full item details |

## Web Dashboard

The dashboard provides visualization and management of your knowledge graph:

- **Interactive Graph**: Force-directed visualization of lesson relationships
- **Usage Heatmap**: Color-coded nodes showing retrieval frequency
- **Session History**: Timeline of queries and retrievals
- **Project Browser**: View and edit project contexts
- **Real-time Updates**: WebSocket-powered live event stream

## Architecture

MGCP uses a hybrid storage approach optimized for different access patterns:

| Storage | Purpose |
|---------|---------|
| **SQLite** | Structured data (lessons, contexts, telemetry) |
| **ChromaDB** | Vector embeddings for semantic search |
| **NetworkX** | In-memory graph for relationship traversal |

Data is stored in `~/.mgcp/` by default:
- `lessons.db` - Lesson content and metadata
- `telemetry.db` - Usage analytics
- `chroma/` - Vector embeddings

See [docs/architecture.html](docs/architecture.html) for the full architecture diagram.

## Development

```bash
# Create virtual environment (if not already done)
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dev dependencies
pip3 install -e ".[dev]"

# Run tests
pytest

# Run specific test
pytest tests/test_basic.py::TestLessonStore

# Run linter
ruff check src/

# Start MCP server directly
python3 -m mgcp.server

# Start web dashboard
python3 -m mgcp.web_server
```

## Project Status

| Phase | Status |
|-------|--------|
| Phase 1: Basic Storage & Retrieval | Complete |
| Phase 2: Semantic Search | Complete |
| Phase 3: Graph Traversal | Complete |
| Phase 4: Refinement & Learning | Complete |
| Phase 5: Proactive Intelligence | Planned |

## API Endpoints

The dashboard exposes a REST API for integration:

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/lessons` | Get all lessons |
| `GET /api/lessons/{id}` | Get specific lesson |
| `PUT /api/lessons/{id}` | Update lesson |
| `DELETE /api/lessons/{id}` | Delete lesson |
| `GET /api/graph` | Get graph data for visualization |
| `GET /api/projects` | List all projects |
| `GET /api/projects/{id}` | Get project context |
| `GET /api/sessions` | Get session history |
| `WS /ws/events` | Real-time event stream |

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache 2.0 - See [LICENSE](LICENSE) for details.

---

Built with the [Model Context Protocol](https://modelcontextprotocol.io/) for seamless LLM integration.
