# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MGCP (Memory Graph Control Protocol)** is a Python MCP server providing persistent, graph-based memory for LLM interactions. The system stores lessons learned during LLM sessions in a graph structure, allowing semantic querying without loading full context histories.

**Status**: v1.0.0 - Implemented and running as a Claude Code SessionStart hook. Phases 1-4 complete.

## Documentation Preferences

When creating diagrams, charts, tables, or other visuals, use HTML documents with visualization libraries:
- **Diagrams/Flowcharts**: mermaid.js
- **Charts/Graphs**: chart.js
- **Tables**: HTML tables with CSS styling

Avoid ASCII art diagrams in documentation. HTML visuals are easier to digest for a wider audience.

## Technology Stack

- Python 3.11+ with virtual environment (`.venv/`)
- FastMCP for MCP server framework
- NetworkX for graph operations
- ChromaDB for vector storage (lessons + catalogue)
- sentence-transformers for local embeddings (`all-MiniLM-L6-v2`)
- Pydantic for data validation
- SQLite + JSON for persistence
- FastAPI for web dashboard

## Development Commands

```bash
# Run the MCP server
python -m mgcp.server

# Run the web UI server
python -m mgcp.web_server

# Run tests
pytest

# Run a single test
pytest tests/test_basic.py::test_name

# Run linter
ruff check src/
```

## Architecture

See `docs/architecture.html` for the interactive architecture diagram.

The system flows from Claude/LLM through MCP Protocol to the MGCP Server, which contains Query Handler, Lesson Manager, and Graph Walker components. These connect to dual storage backends: Graph Store (NetworkX) and Vector Store (ChromaDB).

### Core Components

All source files are in `src/mgcp/`:

- `server.py` - MCP server with 23 tools
- `models.py` - Pydantic models (Lesson, ProjectContext, ProjectCatalogue, SecurityNote, Convention, etc.)
- `graph.py` - NetworkX graph operations with typed relationships
- `vector_store.py` - ChromaDB integration for lesson search
- `catalogue_vector_store.py` - ChromaDB integration for project catalogue search
- `persistence.py` - SQLite/JSON storage for lessons and project contexts
- `telemetry.py` - Usage tracking and analytics
- `web_server.py` - FastAPI web UI for browsing lessons/projects
- `launcher.py` - Unified CLI launcher
- `bootstrap.py` - Initial lesson seeding
- `migrations.py` - Database migrations

### Data Model

**Lessons** have hierarchical relationships (parent/child) and typed cross-links. Key fields:
- `trigger`: When the lesson applies (keywords/patterns)
- `action`: What to do (imperative)
- `tags`: Categorization for retrieval

**Project Contexts** persist across sessions with:
- Todos with status tracking
- Active files being worked on
- Recent decisions
- Notes about current state

**Project Catalogues** store project-specific knowledge:
- Architecture notes and gotchas
- Security notes with severity/status
- Conventions (naming, style, structure)
- File couplings (files that change together)
- Decisions with rationale
- Error patterns with solutions

### MCP Tools (23 total)

**Lesson Discovery & Retrieval:**
- `query_lessons` - Semantic search for relevant lessons
- `get_lesson` - Get full lesson details by ID
- `spider_lessons` - Traverse related lessons from a starting point
- `list_categories` - Browse top-level lesson categories
- `get_lessons_by_category` - Get lessons under a category

**Lesson Management:**
- `add_lesson` - Create a new lesson
- `refine_lesson` - Improve an existing lesson
- `link_lessons` - Create typed relationships between lessons

**Project Context:**
- `get_project_context` - Load saved context for a project
- `save_project_context` - Persist context for next session
- `add_project_todo` - Add a todo item
- `update_project_todo` - Update todo status
- `list_projects` - List all tracked projects

**Project Catalogue:**
- `search_catalogue` - Semantic search across catalogue items
- `add_catalogue_arch_note` - Add architecture note/gotcha
- `add_catalogue_security_note` - Add security consideration
- `add_catalogue_dependency` - Track framework/library/tool
- `add_catalogue_convention` - Document coding conventions
- `add_catalogue_coupling` - Record file dependencies
- `add_catalogue_decision` - Document decisions with rationale
- `add_catalogue_error_pattern` - Record error solutions
- `remove_catalogue_item` - Remove a catalogue item
- `get_catalogue_item` - Get full item details

## Claude Code Integration

Add to Claude Code settings (`~/.config/claude-code/settings.json`):

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

Data is stored in `~/.mgcp/` by default.

## Implementation Roadmap

1. ~~Phase 1: Basic lesson storage and retrieval via MCP~~ Complete
2. ~~Phase 2: Semantic search with embeddings~~ Complete
3. ~~Phase 3: Graph traversal and hierarchical structure~~ Complete
4. ~~Phase 4: Refinement, versioning, and learning loops~~ Complete
5. Phase 5: Intelligence layer with proactive suggestions (planned)
