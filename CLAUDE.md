# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MGCP** (Memory Graph Core Primitives) is a Python MCP server providing persistent, graph-based memory for LLM interactions. The system stores lessons learned during LLM sessions in a graph structure, allowing semantic querying without loading full context histories.

**Status**: v2.1.0 - Alpha/Research project. Phases 1-7 complete, actively dogfooding. Phase 8 (skill compilation) removed — degraded reliability.

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

- `server.py` - MCP server with 37 tools
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
- `bootstrap_loader.py` - Load bootstrap lessons, workflows, and relationships from YAML files
- `logging_config.py` - Centralized logging with rotation (10MB max, 5 backups)
- `reminder_state.py` - Self-directed reminder system for LLM workflow continuity

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

### MCP Tools (43 total)

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

**Soliloquy (2):**
- `write_soliloquy` - Write a reflective message to your future self (at session close/compression)
- `read_soliloquy` - Read your most recent message(s) to yourself (at session start)

**Intent Config (6):**
- `list_intents` - List all configured intents (name, description, action, tag/keyword counts)
- `get_intent` - Get full definition of one intent by name
- `add_intent` - Add a new intent to the routing config (closes the REM growth loop)
- `update_intent` - Update an existing intent's description/action/tags/linked_workflow/keyword_patterns
- `remove_intent` - Delete an intent from the routing config
- `compile_intent_to_skill` - Compile an intent (+ linked workflow + lessons) into a SKILL.md file at `~/.claude/skills/` or `<project>/.claude/skills/`. Walks intent → workflow → ordered steps → lessons-per-step. Skill is a downstream artifact; the intent stays the source of truth.

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

MGCP v2.2 makes the routing prompt **data, not code**. The intent classification system has 8 intent categories (`git_operation`, `catalogue_dependency`, `catalogue_security`, `catalogue_decision`, `catalogue_arch_note`, `catalogue_convention`, `task_start`, `session_end`) defined in `~/.mgcp/intent_config.json`. Both Claude Code hooks and the REM intent_calibration operation read from this file. Adding or modifying an intent no longer requires editing hook code or shipping a release — you edit the JSON (or call `add_intent` / `update_intent` MCP tools, or let REM propose the change) and the next session picks it up automatically.

| Hook | Event | Purpose |
|------|-------|---------|
| `session-init.py` | SessionStart | Inject routing prompt + intent-action map (loaded from `intent_config.json`) plus workflow instructions |
| `user-prompt-dispatcher.py` | UserPromptSubmit | Hard keyword gates (loaded from `intent_config.json` — both git AND session_end fire from one loop), terse routing re-injection, scheduled reminders, workflow state |
| `post-tool-dispatcher.py` | PostToolUse | Routes by tool: Edit/Write triggers knowledge-capture checkpoint; Bash triggers error detection with cooldown |
| `mgcp-precompact.py` | PreCompact | Critical reminder to save context (and write_soliloquy) before context compression |

Both hooks fall back to a minimal hard-coded intent set if the JSON file is missing or corrupt, so a fresh install never crashes. Legacy regex hooks (`git-reminder.py`, `catalogue-reminder.py`, `task-start-reminder.py`) are archived in `examples/claude-hooks/legacy/`.

**Growth loop:** REM intent_calibration runs community detection on the lesson graph and surfaces findings when (a) a community has unmapped tags or (b) a community spans multiple intents with no clear dominant (< 60% share) — the latter check catches misfit clusters that the v2.1 hand-coded `tag_to_intent` map silenced via defensive over-mapping. Findings include structured `proposed_patch` metadata so the LLM (or a future automated writeback path) can call `add_intent`/`update_intent` directly. Lesson community → REM finding → intent_config update → next session's hook injection picks up the new intent. No code commit required.

**Intent → Skill compilation (v2.3):** An intent + its linked workflow + the workflow's per-step lessons can be compiled into an Anthropic-format SKILL.md file at `~/.claude/skills/{intent_name}/SKILL.md` (user scope) or `<project>/.claude/skills/{intent_name}/SKILL.md` (project scope). The compiler walks intent → workflow → ordered steps → lessons-per-step and inlines all four layers into a single self-contained document. Compiled skills give intents two new firing channels — slash commands (`/git_operation`) and Claude's auto-discovery via description matching — on top of MGCP's hook-level keyword gates and LLM intent classification.

**Critical anti-Phase-8 invariant:** compiling a skill is purely additive. It does NOT remove the source intent from `intent_config.json`, it does NOT remove backing lessons from the active query pool, and it does NOT change any MGCP behavior. The intent stays the source of truth and the skill is a downstream artifact you can recompile any time. This is the inverse of Phase 8's failure mode, where graduated lessons were hidden from `query_lessons` (degrading reliability). The web UI badges compiled skills as "fresh" or "stale" by comparing the skill file mtime against the intent_config.json mtime and the backing lessons' `last_refined` timestamps, so users know when to recompile.

## Implementation Roadmap

1. ~~Phase 1: Basic lesson storage and retrieval via MCP~~ Complete
2. ~~Phase 2: Semantic search with embeddings~~ Complete
3. ~~Phase 3: Graph traversal and hierarchical structure~~ Complete
4. ~~Phase 4: Refinement, versioning, and learning loops~~ Complete
5. ~~Phase 5: Quality of Life~~ Complete - Multi-client support, export/import, backup/restore, proactive hooks
6. ~~Phase 6: Proactive Intelligence~~ Complete - Intent-based LLM self-routing, REM intent calibration, workflow state management
7. ~~Phase 7: Feedback Loops~~ Complete - REM cycle engine (staleness scan, duplicate detection, community detection, knowledge extraction), versioned context history, lesson version snapshots, scheduled reminders
8. ~~Phase 8: Skill Compilation~~ **Removed** - Degraded reliability by hiding lessons from active querying. Hook-based injection outperforms skill files.
