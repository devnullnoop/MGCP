# MGCP - Memory Graph Control Protocol

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.11%20|%203.12-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

> **⚠️ Alpha Software / Research Project** — This is a research project exploring ways to extend and improve LLM interactions through persistent memory. We're dogfooding it daily as we build it, which means things work but may change rapidly. APIs are not stable, data formats may evolve, and you might hit rough edges. If you're comfortable with that, welcome aboard! Feedback and bug reports are appreciated.

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

## Screenshots

### Knowledge Graph Dashboard
Interactive visualization of your lesson network with real-time updates, usage heatmaps, and neural firing animations.

![Dashboard](docs/screenshots/dashboard.png)

### Lesson Management
Browse, search, and manage your lessons with hierarchical organization and relationship tracking.

![Lessons](docs/screenshots/lessons.png)

### Project Catalogue
Store project-specific knowledge: architecture notes, security concerns, coding conventions, file couplings, and decisions.

![Projects](docs/screenshots/projects.png)

## Quick Start

### 1. Install MGCP (one time)

```bash
git clone https://github.com/devnullnoop/MGCP.git
cd MGCP

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Option A: Use the install helper (recommended - checks for issues first)
python check_install.py --install

# Option B: Manual installation
pip install --upgrade pip
pip install -e ".[dev]"
mgcp-bootstrap
```

The `check_install.py` script verifies Python version, pip version, and virtual environment before installing. It will auto-fix pip if needed and provide clear guidance for any issues.

> **Note**: Using a virtual environment avoids dependency conflicts with other packages.
>
> **Intel Mac / Older Systems**: If you see `metadata-generation-failed` errors, your pip is too old and trying to build packages from source. Run these commands in order:
> ```bash
> pip install --upgrade pip setuptools wheel
> pip install -e ".[dev]"
> ```
>
> **Conda users**: If you see `"setup.py" not found` errors, run `pip install --upgrade pip` first. Conda environments often have older pip versions that don't support modern Python packaging.

### 2. Configure Your LLM Client

```bash
mgcp-init
```

This auto-detects installed clients and configures them. Or specify manually:

```bash
mgcp-init --client claude-code    # Claude Code only
mgcp-init --client cursor         # Cursor only
mgcp-init --client all            # All supported clients
mgcp-init --list                  # Show supported clients
```

**Supported clients:** Claude Code, Claude Desktop, Cursor, Windsurf, Zed, Continue, Cline, Sourcegraph Cody

Restart your LLM client, and MGCP is ready to use.

> **Claude Code Users**: The init command also creates project hooks that remind the AI to save lessons throughout the session. See [Claude Code Hooks](#claude-code-hooks) for details.

### 3. (Optional) Start the Dashboard

```bash
mgcp-dashboard
# Opens http://127.0.0.1:8765
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
| `mgcp-init` | Configure MGCP for your LLM client (auto-detects installed clients) |
| `mgcp-init --client X` | Configure specific client (use `--list` to see all 8 supported clients) |
| `mgcp-init --verify` | Verify MGCP setup is working correctly |
| `mgcp-init --doctor` | Diagnose Claude Code MCP configuration issues |
| `mgcp-init --project-config` | Also configure project-specific MCP server in ~/.claude.json |
| `mgcp` | Start MCP server (use `mgcp --help` for options) |
| `mgcp-bootstrap` | Seed database with initial lessons |
| `mgcp-dashboard` | Start web dashboard on port 8765 |
| `mgcp-export lessons -o FILE` | Export all lessons to JSON |
| `mgcp-import FILE` | Import lessons from JSON |
| `mgcp-duplicates` | Find duplicate lessons by semantic similarity |
| `mgcp-backup` | Backup data to archive (use `--restore` to restore) |

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
- `lessons.db` - Lessons, project contexts, and usage telemetry
- `chroma/` - Vector embeddings for semantic search

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
| Phase 5: Quality of Life | Complete |
| Phase 6: Proactive Intelligence | Planned |

### Phase 5 Features (Complete)
- [x] Multi-client support (8 clients: Claude Code, Claude Desktop, Cursor, Windsurf, Zed, Continue, Cline, Cody)
- [x] Export/import lessons (`mgcp-export`, `mgcp-import`)
- [x] Duplicate lesson detection (`mgcp-duplicates`)
- [x] Backup and restore (`mgcp-backup`, `mgcp-backup --restore`)
- [x] Auto-tagging suggestions (`suggest_tags` in data_ops)
- [x] Lesson usage analytics dashboard (heatmap visualization)
- [x] Project deduplication migration (unique constraint on project_path)

### Phase 6 Features (Planned)
- Auto-suggest lessons from conversations
- Feedback loop (track which lessons were helpful)
- Git integration (parse commits/PRs for learnings)
- Cross-project global lessons
- Lesson templates (Error→Solution, Gotcha→Workaround)
- Lesson quality scoring

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

## Claude Code Hooks

For Claude Code users, `mgcp-init` creates project-level hooks that help the AI remember to use MGCP throughout the session:

| Hook | Trigger | Purpose |
|------|---------|---------|
| `session-init.py` | Session start | Prompts AI to load project context and query relevant lessons |
| `git-reminder.py` | User mentions "commit", "push", "git" | Reminds AI to query lessons before git operations |
| `catalogue-reminder.py` | User mentions libraries, security, decisions | Reminds AI to catalogue dependencies, security notes, decisions |
| `task-start-reminder.py` | User says "fix", "implement", "work on" | Reminds AI to query lessons and workflows before starting tasks |
| `mgcp-reminder.py` | After Edit/Write | Short reminder to save lessons when learning something new |
| `mgcp-precompact.py` | Before context compression | **Critical** reminder to save all lessons before context is lost |

All hooks are Python scripts for cross-platform compatibility (Windows, macOS, Linux).

These hooks are created in your project's `.claude/` directory:
```
.claude/
├── hooks/
│   ├── session-init.py
│   ├── git-reminder.py
│   ├── catalogue-reminder.py
│   ├── task-start-reminder.py
│   ├── mgcp-reminder.py
│   └── mgcp-precompact.py
└── settings.json
```

The hooks ensure the AI actively uses MGCP rather than forgetting about it mid-session.

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache 2.0 - See [LICENSE](LICENSE) for details.

---

Built with the [Model Context Protocol](https://modelcontextprotocol.io/) for seamless LLM integration.
