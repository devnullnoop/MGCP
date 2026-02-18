# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MGCP** (Memory Graph Core Primitives) is a Python MCP server providing persistent, graph-based memory for LLM interactions. The system stores lessons learned during LLM sessions in a graph structure, allowing semantic querying without loading full context histories.

**Status**: v2.0.0 - Alpha/Research project. Phases 1-7 complete, actively dogfooding.

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
- Qdrant for vector storage (lessons + catalogue + workflows)
- sentence-transformers for local embeddings (`BAAI/bge-base-en-v1.5`, 768 dimensions)
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

## Data Management Commands

```bash
# Backup MGCP data
mgcp-backup                          # Create backup in current directory
mgcp-backup -o backup.tar.gz         # Specify output file
mgcp-backup --list                   # Preview what would be backed up
mgcp-backup --restore backup.tar.gz  # Restore from backup

# Export/Import lessons
mgcp-export lessons -o lessons.json  # Export lessons to JSON
mgcp-export projects -o proj.json    # Export project contexts
mgcp-import lessons.json             # Import lessons (skips duplicates)
mgcp-import data.json --merge overwrite  # Overwrite duplicates
mgcp-import data.json --dry-run      # Preview import without changes

# Find duplicate lessons
mgcp-duplicates                      # Find similar lessons (0.85 threshold)
mgcp-duplicates -t 0.90              # Higher threshold for stricter matching

# Bootstrap lessons and workflows
mgcp-bootstrap                       # Seed all (core + dev)
mgcp-bootstrap --update-triggers     # Update trigger fields on existing lessons

# Migrate from ChromaDB to Qdrant (for existing installations)
mgcp-migrate                         # Migrate data to Qdrant
mgcp-migrate --dry-run               # Preview what would be migrated
mgcp-migrate --force                 # Overwrite existing Qdrant data
```

## Architecture

The system flows from Claude/LLM through MCP Protocol to the MGCP Server, which contains Query Handler, Lesson Manager, and Graph Walker components. These connect to dual storage backends: Graph Store (NetworkX) and Vector Store (Qdrant).

### Core Components

All source files are in `src/mgcp/`:

- `server.py` - MCP server with 35 tools
- `models.py` - Pydantic models (Lesson, ProjectContext, ProjectCatalogue, SecurityNote, Convention, etc.)
- `graph.py` - NetworkX graph operations with typed relationships and Louvain community detection
- `embedding.py` - Centralized BGE embedding model (`BAAI/bge-base-en-v1.5`)
- `qdrant_vector_store.py` - Qdrant integration for lesson, workflow, and community summary search
- `qdrant_catalogue_store.py` - Qdrant integration for project catalogue search
- `persistence.py` - SQLite/JSON storage for lessons, project contexts, and community summaries
- `telemetry.py` - Usage tracking and analytics
- `web_server.py` - FastAPI web UI for browsing lessons/projects
- `launcher.py` - Unified CLI launcher
- `bootstrap.py` - Initial lesson seeding
- `migration.py` - ChromaDB to Qdrant migration tool
- `migrations.py` - Database migrations
- `init_project.py` - Multi-client MCP configuration (8 LLM clients supported)
- `data_ops.py` - Export, import, and duplicate detection
- `rem_cycle.py` - REM (Recalibrate Everything in Memory) cycle engine
- `rem_config.py` - REM scheduling strategies (linear, fibonacci, logarithmic)
- `backup.py` - Backup and restore functionality

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

### MCP Tools (35 total)

**Lesson Discovery & Retrieval (5):**
- `query_lessons` - Semantic search for relevant lessons
- `get_lesson` - Get full lesson details by ID
- `spider_lessons` - Traverse related lessons from a starting point
- `list_categories` - Browse top-level lesson categories
- `get_lessons_by_category` - Get lessons under a category

**Lesson Management (4):**
- `add_lesson` - Create a new lesson
- `refine_lesson` - Improve an existing lesson
- `link_lessons` - Create typed relationships between lessons
- `delete_lesson` - Remove a lesson from all stores (SQLite, Qdrant, NetworkX)

**Project Context (5):**
- `get_project_context` - Load saved context for a project
- `save_project_context` - Persist context for next session
- `add_project_todo` - Add a todo item
- `update_project_todo` - Update todo status
- `list_projects` - List all tracked projects

**Project Catalogue (4):**
- `search_catalogue` - Semantic search across catalogue items
- `add_catalogue_item` - Add any catalogue item (arch, security, library, convention, coupling, decision, error, or custom type)
- `remove_catalogue_item` - Remove a catalogue item
- `get_catalogue_item` - Get full item details

**Workflows (8):**
- `list_workflows` - List all available workflows
- `query_workflows` - Semantic match task to workflows
- `get_workflow` - Get workflow with all steps and linked lessons
- `get_workflow_step` - Get step details with expanded lessons
- `create_workflow` - Create a new workflow
- `update_workflow` - Update workflow metadata/triggers
- `add_workflow_step` - Add a step to a workflow
- `link_lesson_to_workflow_step` - Link lesson to workflow step

**Community Detection (3):**
- `detect_communities` - Auto-detect topic clusters using Louvain algorithm
- `save_community_summary` - Persist LLM-generated summary for a community
- `search_communities` - Semantic search across community summaries

**REM Cycle (3):**
- `rem_run` - Run consolidation operations (staleness, duplicates, communities)
- `rem_report` - View last cycle's findings
- `rem_status` - Show schedule state and what's due

**Workflow State (1):**
- `update_workflow_state` - Update active workflow, current step, and completion status

**Reminder Control (2):**
- `schedule_reminder` - Schedule self-directed reminders for workflow continuity
- `reset_reminder_state` - Reset reminder state to defaults

## Claude Code Integration

Add to Claude Code MCP config (`~/.claude.json`):

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

## Claude Code Hooks

MGCP v2.0 uses intent-based LLM self-routing instead of regex pattern matching. The LLM classifies each user message into 7 intent categories and follows an intent-action map to call the right tools.

| Hook | Event | Purpose |
|------|-------|---------|
| `session-init.py` | SessionStart | Inject routing prompt, intent-action map, workflow instructions (~800 tokens) |
| `user-prompt-dispatcher.py` | UserPromptSubmit | Scheduled reminders + workflow state injection (zero regex, ~60 lines) |
| `mgcp-reminder.py` | PostToolUse (Edit/Write) | Prompt to capture patterns and gotchas after code changes |
| `mgcp-precompact.py` | PreCompact | Critical reminder to save before context compression |

Legacy hooks (`git-reminder.py`, `catalogue-reminder.py`, `task-start-reminder.py`) are archived in `examples/claude-hooks/legacy/`.

The key insight: LLM self-routing (87% accuracy) outperforms regex (58%), is simpler (~130 lines vs ~380), and injects fewer tokens (~800 vs ~2000). Intent calibration via the REM cycle continuously refines the routing prompt using community detection.

## Implementation Roadmap

1. ~~Phase 1: Basic lesson storage and retrieval via MCP~~ Complete
2. ~~Phase 2: Semantic search with embeddings~~ Complete
3. ~~Phase 3: Graph traversal and hierarchical structure~~ Complete
4. ~~Phase 4: Refinement, versioning, and learning loops~~ Complete
5. ~~Phase 5: Quality of Life~~ Complete - Multi-client support, export/import, backup/restore, proactive hooks
6. ~~Phase 6: Proactive Intelligence~~ Complete - Intent-based LLM self-routing, REM intent calibration, workflow state management
7. ~~Phase 7: Feedback Loops~~ Complete - REM cycle engine (staleness scan, duplicate detection, community detection, knowledge extraction), versioned context history, lesson version snapshots, scheduled reminders
