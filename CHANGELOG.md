# Changelog

All notable changes to MGCP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-02-10

### Added
- **Intent-based LLM self-routing**: Replaced regex-based hook dispatching with semantic intent classification (87% accuracy vs 58% for regex). 7 intent categories with an intent-action map injected at session start (~800 tokens vs ~2000)
- **REM cycle**: Periodic knowledge consolidation with 3 new MCP tools (`rem_run`, `rem_report`, `rem_status`). Operations: staleness scan, duplicate detection, community detection, knowledge extraction, context summary
- **Versioned context history**: `save_project_context` now appends snapshots to `context_history` table with catalogue delta tracking
- **Lesson version history**: `refine_lesson` now snapshots previous version into `lesson_versions` table before overwriting
- **Multi-strategy scheduling**: REM operations run on independent schedules - linear (staleness), fibonacci (community detection), logarithmic (knowledge extraction)
- **Interactive findings**: REM cycle produces structured findings with selectable options for human-in-the-loop review
- **Backfill migrations**: Existing lessons and projects get version history records on upgrade (migrations 5-7)
- **Workflow state management**: `update_workflow_state` tool for tracking active workflow and step progress
- **Scheduled reminders**: `schedule_reminder` and `reset_reminder_state` tools for self-directed workflow continuity
- **Global hooks**: `mgcp-init` now deploys hooks globally to `~/.mgcp/hooks/` + `~/.claude/settings.json` by default, so hooks fire in every Claude Code session without per-project deployment. Project-local hooks available via `--local` flag
- **Portable hook templates**: Hook scripts live in `src/mgcp/hook_templates/` as standalone Python files, deployable via `mgcp-init`

### Changed
- **Hook system**: Rewritten from 3 regex-based hooks (~380 lines) to 4 intent-based hooks (~130 lines). Legacy hooks archived in `examples/claude-hooks/legacy/`
- **Default hook deployment**: `mgcp-init` now deploys global hooks by default instead of project-local. Use `--local` for per-project deployment
- **Tool count**: 38 → 42 MCP tools

## [1.2.0] - 2026-02-07

### Added
- **Community detection**: 3 new MCP tools (`detect_communities`, `save_community_summary`, `search_communities`) using Louvain algorithm for auto-clustering lessons into topic groups
- **Community summary sync**: Improved query bridging between community summaries and lesson search
- **BGE instruction prefix**: Query embeddings now use BGE-recommended instruction prefix for better retrieval accuracy
- **YAML bootstrap**: Bootstrap lessons and workflows defined in YAML files for easier editing and review
- **GitHub Actions publish workflow**: Automated PyPI publishing on tag push using trusted publishing (OIDC)

### Changed
- **Bootstrap format**: Migrated from Python dictionaries to YAML files (`src/mgcp/bootstrap_data/`)
- **Trigger format**: Converted bootstrap triggers from keyword bags to narrative descriptions for better BGE embedding quality
- **Tool count**: 35 → 38 MCP tools (CLAUDE.md updated)

### Fixed
- **UNIQUE constraint failure**: `save_project_context` no longer fails on legacy project IDs with different path formats
- **Embedding timeout**: Deferred embedding model load prevents MCP connection timeout on startup
- **CI lint**: Use `StrEnum` instead of `str + Enum` pattern for Python 3.11+ compatibility
- **Stale references**: Removed ruff per-file-ignores for deleted bootstrap Python files
- **README hooks table**: Added missing hook entries
- **Test stability**: Marked embedding-heavy tests as slow to fix CI timeout; fixed lint error in test file

## [1.1.0] - 2026-01-21

First tagged release. Includes all changes since initial development.

### Added
- **Qdrant vector store**: Replaced ChromaDB with Qdrant for vector storage
  - Same API for local mode and server mode (growth path for production)
  - Local mode: `~/.mgcp/qdrant` (no server required)
  - Server mode: Connect to Qdrant server (same API)
- **BGE embedding model**: Upgraded from `all-MiniLM-L6-v2` (384 dim) to `BAAI/bge-base-en-v1.5` (768 dim)
  - ~7% better retrieval quality (MTEB benchmark)
  - First run downloads ~415MB model
- **Migration tool**: `mgcp-migrate` command for ChromaDB → Qdrant migration
  - `--dry-run` to preview what would be migrated
  - `--force` to overwrite existing Qdrant data
  - Re-embeds all data with new BGE model
- **Centralized embedding**: New `embedding.py` module with shared model instance
- **delete_lesson MCP tool**: Complete lesson deletion from all stores (SQLite, Qdrant, NetworkX)
- **Proactive Hooks**: UserPromptSubmit hooks detect keywords and inject reminders
  - Phase-based dispatcher for workflow state tracking
  - Git, catalogue, and task-start reminder hooks
- **Multi-client support**: 8 LLM clients (Claude Code, Claude Desktop, Cursor, Windsurf, Zed, Continue, Cline, Cody)
- **Data export/import**: `mgcp-export` and `mgcp-import` commands for lesson portability
- **Backup/restore**: `mgcp-backup` command with `--restore` option
- **Duplicate detection**: `mgcp-duplicates` command finds semantically similar lessons
- **Release versioning workflow**: 6-step workflow for semantic versioning releases
- Documentation for all 35 MCP tools in CLAUDE.md

### Changed
- **Vector store backend**: ChromaDB → Qdrant
  - `qdrant_vector_store.py` replaces `vector_store.py`
  - `qdrant_catalogue_store.py` replaces `catalogue_vector_store.py`
- **Embedding model**: `all-MiniLM-L6-v2` → `BAAI/bge-base-en-v1.5`
- **Dependencies**: Removed chromadb, added qdrant-client
- **Hook system**: Rewritten to proof-based gates instead of instructional reminders
- Ruff linter config: line-length 120, per-file ignores for tests/bootstrap

### Fixed
- Qdrant dual-client lock bug (shared client pattern)
- Import method name bug (`save_lesson` → `add_lesson`)
- All linter errors resolved (305 errors fixed)
- Duplicate project bug with database migration
- CI test failures from ChromaDB migration

### Removed
- ChromaDB vector stores (`vector_store.py`, `catalogue_vector_store.py`) - replaced by Qdrant

### Migration Required
For existing installations with ChromaDB data:
```bash
mgcp-migrate              # Migrates ChromaDB data to Qdrant
mgcp-migrate --dry-run    # Preview first
```

## [1.0.0] - 2026-01-06

### Added
- Initial public release of MGCP (Memory Graph Core Primitives)
- 23 MCP tools for lesson and project management
- Semantic search using ChromaDB and sentence-transformers
- Graph-based lesson relationships using NetworkX
- Project context persistence across sessions
- Project catalogue for architecture notes, conventions, decisions, etc.
- Web dashboard with real-time visualization
- Claude Code SessionStart hook for automatic context loading
- Comprehensive test suite

### Features
- **Lesson Memory**: Store, query, and traverse lessons learned during LLM sessions
- **Project Context**: Save and restore project state including todos, decisions, and active files
- **Project Catalogue**: Document architecture, security notes, conventions, file couplings, and error patterns
- **Semantic Search**: Find relevant lessons using natural language queries
- **Graph Traversal**: Explore related knowledge through typed relationships
- **Usage Analytics**: Track lesson usage for continuous improvement

### Technical
- Python 3.11+ support
- FastMCP for MCP server framework
- SQLite for structured data persistence
- ChromaDB for vector embeddings
- FastAPI for web dashboard
- WebSocket support for real-time updates

## Development History

### Phase 1: Basic Storage (Complete)
- Core lesson model and persistence
- SQLite database schema
- Basic CRUD operations

### Phase 2: Semantic Search (Complete)
- ChromaDB integration
- sentence-transformers embeddings
- Query relevance scoring

### Phase 3: Graph Traversal (Complete)
- NetworkX graph structure
- Parent/child relationships
- Related lesson traversal

### Phase 4: Refinement & Learning (Complete)
- Typed relationships (prerequisite, alternative, etc.)
- Lesson versioning
- Project catalogue system
- Telemetry and analytics

### Phase 5: Quality of Life (Complete)
- Multi-client support (8 LLM clients)
- Export/import lessons
- Backup and restore
- Duplicate detection
- Proactive hooks (UserPromptSubmit)

### Phase 6: Proactive Intelligence (Complete)
- Intent-based LLM self-routing (replaces regex hooks, 87% accuracy)
- REM cycle engine (staleness scan, duplicate detection, community detection, knowledge extraction)
- Versioned context history and lesson version snapshots
- Workflow state management and scheduled reminders
- Community detection with Louvain algorithm
