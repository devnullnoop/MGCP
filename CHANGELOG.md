# Changelog

All notable changes to MGCP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- Documentation for delete_lesson in CLAUDE.md (35 tools now documented)

### Changed
- **Vector store backend**: ChromaDB → Qdrant
  - `qdrant_vector_store.py` replaces `vector_store.py`
  - `qdrant_catalogue_store.py` replaces `catalogue_vector_store.py`
- **Embedding model**: `all-MiniLM-L6-v2` → `BAAI/bge-base-en-v1.5`
- **Dependencies**: Removed chromadb, added qdrant-client
- **Renamed internal methods for clarity**:
  - `vector_store.remove_lesson()` → `vector_store.remove_vector_lesson()` - Qdrant layer
  - `graph.remove_lesson()` → `graph.remove_graph_lesson()` - NetworkX layer
  - `persistence.delete_lesson()` remains unchanged - SQLite layer
- Updated tests to use new method names

### Deprecated
- ChromaDB vector stores (`vector_store.py`, `catalogue_vector_store.py`) - kept for migration

### Migration Required
For existing installations with ChromaDB data:
```bash
mgcp-migrate              # Migrates ChromaDB data to Qdrant
mgcp-migrate --dry-run    # Preview first
```

## [1.1.0] - 2026-01-07

### Added
- **Proactive Hooks**: UserPromptSubmit hooks detect keywords and inject reminders:
  - `git-reminder.py`: Detects git operations, reminds to query lessons
  - `catalogue-reminder.py`: Detects library/security/decision mentions, reminds to catalogue
- **Multi-client support**: 8 LLM clients now supported (Claude Code, Claude Desktop, Cursor, Windsurf, Zed, Continue, Cline, Cody)
- **Data export/import**: `mgcp-export` and `mgcp-import` commands for lesson portability
- **Backup/restore**: `mgcp-backup` command with `--restore` option for full data backup
- **Duplicate detection**: `mgcp-duplicates` command finds semantically similar lessons
- **Project deduplication**: Unique constraint on project_path prevents duplicate contexts

### Changed
- Ruff linter config updated: line-length 120, per-file ignores for tests/bootstrap
- Phase 5 marked complete

### Fixed
- All linter errors resolved (305 errors fixed)
- Duplicate project bug fixed with database migration

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

### Phase 6: Proactive Intelligence (Planned)
- Automatic lesson suggestions
- Pattern detection
- Cross-project knowledge transfer
