# Changelog

All notable changes to MGCP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- Initial public release of MGCP (Memory Graph Control Protocol)
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
