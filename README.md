# LLMprint - Memory Graph Core Primitives (MGCP)

[![License](https://img.shields.io/badge/License-O'Saasy-blue.svg)](https://osaasy.dev/)
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

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/architecture-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/architecture-light.png">
  <img alt="MGCP Architecture" src="docs/screenshots/architecture-dark.png" width="700">
</picture>

MGCP provides **34 MCP tools** that let your LLM:
- Query relevant lessons semantically before starting any task
- Store new lessons as they're discovered
- Traverse related knowledge through the graph
- Save and restore project-specific context between sessions

## Why MGCP Instead of Custom Agents?

Many developers try to solve the "LLM forgetting things" problem by building custom agents—specialized prompts with baked-in knowledge for specific tasks. A "code review agent" with review guidelines. A "debugging agent" with troubleshooting steps.

**The insight: MGCP already does what custom agents would do—without the extra abstraction layer.**

| Approach | Custom Agents | MGCP |
|----------|--------------|------|
| Task-specific guidance | Baked into agent prompt | Query lessons by task type |
| Learning from mistakes | Manually update agent prompt | Add lesson, immediately available |
| Cross-task knowledge | Duplicate across agents | Single lesson, surfaces everywhere |
| Refinement | Edit prompt, redeploy | Refine lesson, instant update |
| Complexity | Agent orchestration layer | Just queries |

When you query `"code review best practices"`, you get code review lessons. Query `"debugging authentication"`, you get those lessons. The knowledge surfaces based on context, not agent selection.

**The benefits:**
- **No prompt engineering fragility** — Lessons are data, not delicate prompt text
- **No synchronization problem** — One source of truth, not knowledge duplicated across agents
- **Immediate learning** — Add a lesson now, it's available in 10 seconds
- **Composable knowledge** — Lessons combine naturally; agents require explicit orchestration

If you're spending time building custom agents to encode task-specific knowledge, consider whether MGCP's lesson system already solves your problem more simply.

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

## Beyond Software Development

**MGCP's architecture is domain-agnostic.** The bootstrap lessons we ship focus on software development because that's our primary use case, but the underlying system works for any domain where an LLM benefits from persistent, contextual memory.

The core primitives are universal:
- **Lessons**: Knowledge with triggers ("when X") and actions ("do Y") - works for any domain
- **Workflows**: Step-by-step processes with contextual guidance - works for any process
- **Catalogue**: Context-specific knowledge - works for any bounded context
- **Relationships**: Connected knowledge that surfaces together - works for any knowledge graph

### Alternative Bootstrap Examples

| Domain | Bootstrap Focus |
|--------|-----------------|
| **Customer Service** | Escalation triggers, issue resolution patterns, customer preference learning |
| **Personal Assistant** | User preferences, scheduling patterns, communication style, recurring tasks |
| **Medical Triage** | Symptom assessment workflows, urgency classification, follow-up protocols |
| **Sales** | Objection handling, customer profiling, deal stage guidance, competitive intelligence |
| **Education** | Learning style adaptation, concept explanation strategies, progress tracking |
| **Legal** | Document review workflows, clause risk patterns, precedent lookup |
| **Research** | Literature review workflows, methodology checklists, citation patterns |

### Building Your Own Bootstrap

To create a domain-specific MGCP deployment:

1. **Fork the bootstrap**: Copy `src/mgcp/bootstrap.py` and replace the lessons
2. **Define your lessons**: Create lessons with domain-specific triggers and actions
3. **Build workflows**: Encode your domain's common processes as workflows
4. **Link knowledge**: Create relationships between related concepts

The same `query_lessons`, `get_workflow`, and `save_project_context` tools work regardless of domain. The semantic search finds relevant knowledge based on the query, not based on hard-coded assumptions about what you're doing.

**Example: A chatbot personalization bootstrap might include:**
- Lessons about conversation style preferences ("When user prefers formal language, avoid slang and contractions")
- Workflows for onboarding new users ("Introduce features gradually, not all at once")
- Catalogue items for user-specific facts ("User's timezone is PST, prefers morning scheduling")

This flexibility means MGCP can serve as the memory backbone for any LLM application that benefits from learning over time.

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

### Lesson Discovery & Retrieval (5 tools)
| Tool | Purpose |
|------|---------|
| `query_lessons` | Semantic search for relevant lessons |
| `get_lesson` | Get full lesson details by ID |
| `spider_lessons` | Traverse related lessons from a starting point |
| `list_categories` | Browse top-level lesson categories |
| `get_lessons_by_category` | Get lessons under a category |

### Lesson Management (3 tools)
| Tool | Purpose |
|------|---------|
| `add_lesson` | Create a new lesson |
| `refine_lesson` | Improve an existing lesson |
| `link_lessons` | Create typed relationships between lessons |

### Project Context (5 tools)
| Tool | Purpose |
|------|---------|
| `get_project_context` | Load saved context for a project |
| `save_project_context` | Persist context for next session |
| `add_project_todo` | Add a todo item |
| `update_project_todo` | Update todo status |
| `list_projects` | List all tracked projects |

### Project Catalogue (11 tools)
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
| `add_catalogue_custom_item` | Add flexible custom catalogue item |
| `remove_catalogue_item` | Remove a catalogue item |
| `get_catalogue_item` | Get full item details |

### Workflows (8 tools)
| Tool | Purpose |
|------|---------|
| `list_workflows` | List all available workflows |
| `query_workflows` | Semantic match task to workflows |
| `get_workflow` | Get workflow with all steps and linked lessons |
| `get_workflow_step` | Get step details with expanded lessons |
| `create_workflow` | Create a new workflow |
| `update_workflow` | Update workflow metadata/triggers |
| `add_workflow_step` | Add a step to a workflow |
| `link_lesson_to_workflow_step` | Link lesson to workflow step |

### Reminder Control (2 tools)
| Tool | Purpose |
|------|---------|
| `set_reminder_boundary` | LLM-controlled suppression of hook reminders |
| `reset_reminder_state` | Reset reminder state to defaults |

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

See `docs/architecture.html` for the full architecture diagram (viewable locally or via the web dashboard).

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

[O'Saasy License](https://osaasy.dev/) - Free to use, modify, and distribute. Commercial SaaS rights reserved by the copyright holder. See [LICENSE.md](LICENSE.md) for details.

---

Built with the [Model Context Protocol](https://modelcontextprotocol.io/) for seamless LLM integration.
