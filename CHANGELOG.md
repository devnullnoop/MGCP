# Changelog

All notable changes to MGCP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (v2.3 hook templates — PreToolUse enforcement)
- **`src/mgcp/hook_templates/pre-tool-dispatcher.py`**: First ENFORCING MGCP hook. Prior hooks (SessionStart, UserPromptSubmit, PostToolUse, PreCompact) are all advisory — they inject text as `<system-reminder>` tags that the LLM may skim or ignore. PreToolUse returns `permissionDecision: "deny"` and the tool call is refused by the Claude Code harness. First enforced rule: `git commit` / `git push` is blocked unless `mcp__mgcp__query_lessons` ran in the same turn. Bypass token `MGCP_BYPASS` in the user prompt disables enforcement for that turn.
- **Quote-aware command detection**: the detector uses `shlex.shlex(..., punctuation_chars=True)` + `whitespace_split=True` so quoted strings stay as single tokens and shell operators (`;`, `&&`, `||`, `|`, `()`) become their own tokens. `grep 'git commit' docs/` and `echo "how to git commit"` correctly pass through; `make build && git push` correctly blocks.
- **Per-turn state in `workflow_state.json`**: UserPromptSubmit resets `turn_query_lessons_called = False` every message and sets `turn_bypass` from the prompt. PostToolUse flips `turn_query_lessons_called = True` when the tool runs. PreToolUse reads both.
- **`docs/mgcp-interception-flow.html`**: Comprehensive mermaid.js flowchart of all 5 hook interception points (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact), the growth loop, data stores touched per session, and 10 known gaps with remediation ideas. Per project convention (HTML over ASCII art).
- **`tests/test_pre_tool_dispatcher.py`**: 14 tests covering detector true/false positives (including the quoted-string false-positive the naive regex hit during bring-up), subprocess-level deny/allow/bypass flows, and fail-open behavior on malformed state or input.

### Changed (v2.3 hook templates)
- **`hook_templates/VERSION`**: 2.2 → 2.3. The installer auto-upgrades installed hooks in `~/.mgcp/hooks/` when the on-disk marker is behind the template version (previously required `--force`, which was the root cause of the silent v2.0 → v2.2 drift).
- **`V2_HOOK_FILES`** in `init_project.py` registers the new `pre-tool-dispatcher.py` under `PreToolUse`.
- **`post-tool-dispatcher.py`** now flips the turn_query_lessons_called flag on `mcp__mgcp__query_lessons` invocations.
- **`user-prompt-dispatcher.py`** resets per-turn state and detects the `MGCP_BYPASS` token.

### Added (v2.3 — intent → skill compiler)
- **`src/mgcp/skill_compiler.py`**: Compiles MGCP intents into Anthropic-format SKILL.md files at `~/.claude/skills/{name}/SKILL.md` (user scope) or `<project>/.claude/skills/{name}/SKILL.md` (project scope). Walks `intent → linked_workflow → ordered steps → lessons-per-step` and inlines all four layers into a single self-contained document. Includes a generation header marking the file as machine-generated, gate_message rendered as a STOP preamble, action template, full workflow with checklists and lesson actions/rationales inlined per step, and a categorization tags footer.
- **`linked_workflow: str | None`** field on `IntentDefinition` so intents can declare which workflow's steps should be inlined when compiling. Optional and explicit (rather than discovered at compile time via semantic match) so compilation is reproducible across embedding model updates.
- **`compile_intent_to_skill` MCP tool**: New tool wrapping the compiler with a markdown response summary (path, byte count, source intent, linked workflow, step/lesson counts). Tool count 42 → 43.
- **Web API**: `POST /api/intent-config/intents/{name}/compile` (compile + return CompileResult), `GET /api/intent-config/intents/{name}/skill-status` (returns whether a skill exists and whether it's stale relative to backing lessons or the intent config).
- **`/intents` page**: New "Compile to skill" button on each intent row. Skill status badges — green "skill: fresh" when up to date, orange "skill: stale" when a backing lesson or the intent itself has been refined since the last compile. Linked workflow ID field added to the edit modal.
- **`tests/test_skill_compiler.py`**: 17 tests covering compilation without/with linked workflow, missing workflow degradation, missing lesson degradation, scope handling (user/project/invalid), unknown intent error, find_skill_path, is_skill_stale, recompile overwrite, and the critical immutability invariant — compiling does NOT remove the source intent from `intent_config.json` or remove backing lessons from the active query pool.
- **`is_skill_stale` helper** that compares the SKILL.md mtime against the `intent_config.json` mtime and the `last_refined` timestamp of every lesson reachable through the linked workflow.

### Changed (v2.3)
- **Tool count**: 42 → 43 MCP tools (`compile_intent_to_skill` added).
- **`IntentDefinition` model**: gained the optional `linked_workflow` field. Existing `intent_config.json` files without this field still load (backward compatible — defaults to `None`).
- **`/intents` page**: card layout now reserves space for the skill status badge between the keyword gate badge and the new Compile button. Edit modal adds a Linked workflow ID input.

### Notes (v2.3)
- v2.3 explicitly avoids the Phase 8 failure mode. Compiling a skill is purely additive: the intent stays in `intent_config.json`, the lessons stay in the active query pool, and MGCP behavior is unchanged. The skill is a downstream artifact you can recompile or delete at any time. This is enforced by the `TestImmutability` test class in `test_skill_compiler.py`.
- A future automated writeback path can call `compile_intent_to_skill` from REM (e.g. as a "promote stable intent to skill" finding) without risking the kind of silent knowledge hiding that broke Phase 8.

### Added
- **Routing prompt as data** (v2.2): The intent classification system is no longer hard-coded across hooks and `rem_cycle._intent_calibration`. New `src/mgcp/intent_config.py` is the single source of truth — Pydantic models, default intents, load/save for `~/.mgcp/intent_config.json`. Save writes a pre-rendered cache (`session_init_routing`, `session_init_actions`, `dispatcher_routing`, `keyword_gates`) so the standalone hook scripts can read strings without importing the mgcp package. Editing the JSON (or calling the new MCP tools below) changes routing behavior on the next user message — no code change, no release.
- **`session_end` intent**: New intent with both a tag mapping (`session-discipline`, `farewell`, `save-context`, etc.) and a hard keyword gate (`bye bye`, `goodbye`, `signing off`, `wrapping up`, `gotta go`, etc.). Fires the dispatcher gate to force `save_project_context` + `write_soliloquy` BEFORE the LLM responds with a farewell. Fixes the v2.1 failure mode where farewells were silently classified as `none` and the LLM waved back without saving anything.
- **Coherence check in REM intent_calibration**: New finding type fires when a community's tags spread across multiple intents with no clear dominant (< 60% share). Catches misfit clusters that the v2.1 hand-coded `tag_to_intent` map silenced via defensive over-mapping (e.g. `session-discipline` and `discipline` were force-mapped to `task_start`, hiding the missing `session_end` intent). Findings include structured `proposed_patch` metadata so a future automated writeback path can apply REM's recommendations directly.
- **5 new MCP tools** for managing intent_config from chat: `list_intents`, `get_intent`, `add_intent`, `update_intent`, `remove_intent`. Closes the growth loop the architectural fix promised: lesson community → REM finding → LLM applies patch → next session's hook injection picks up the new intent.
- **Web UI: `/intents` page** with table view, edit, add, and delete actions. New REST endpoints under `/api/intent-config`. Nav link added from the dashboard.
- **`tests/test_intent_config.py`** — 21 tests covering default config (including regression tests for the v2.1 tag mapping bugs), rendering, persistence (round-trip + corrupt-file fallback), and extensibility (custom intent injection).
- **Soliloquy lifecycle**: `read_soliloquy` step at session start, `write_soliloquy` baked into the new `session_end` intent's action and the precompact hook's reminder, migration 9 creates the `soliloquies` table.

### Changed
- **Tool count**: 37 → 42 MCP tools (5 new intent_config tools)
- **Hooks read JSON, not f-strings**: `session-init.py` and `user-prompt-dispatcher.py` rewritten to load pre-rendered prompt sections from `~/.mgcp/intent_config.json`. The dispatcher's hard keyword gate loop is now a single iteration over `rendered.keyword_gates` — both `git_operation` and `session_end` fire from the same code path. Both hooks fall back to a minimal hard-coded set if the JSON is missing or corrupt, so a fresh install never crashes.
- **`rem_cycle._intent_calibration` deletes the 100-line hand-coded `tag_to_intent` dict** and loads from `intent_config.load_config()` instead.
- **CLAUDE.md and README.md hooks sections** rewritten to describe v2.2 routing-as-data, the growth loop, and the new `session_end` intent.

### Fixed
- **Phase 8 leftover in `tests/test_rem_scheduling.py`**: `test_all_operations_have_schedules` no longer expects `skill_readiness`/`skill_drift_detection` in `DEFAULT_SCHEDULES`. This was the test failure that turned `ff375bc` red on CI.

### Removed
- **Skill Compilation** (Phase 8): Removed entirely — skill compilation degraded reliability by hiding lessons from active querying via graduation filtering. Hook-based knowledge injection outperforms skill files.
  - Removed 3 MCP tools: `compile_skill`, `list_compiled_skills`, `ungraduate_skill`
  - Deleted `skill_compiler.py`, `skill_cli.py`, `skills.html`
  - Removed `graduated_to` filtering from Qdrant vector search and community bridge queries
  - Removed `skill_readiness` and `skill_drift_detection` REM operations
  - Removed `mgcp-compile-skills` CLI entry point
  - Database columns (`graduated_to`, `compiled_skills` table) left in place for backwards compatibility

## [2.1.0] - 2026-02-27

### Added
- **Skill Compilation** (Phase 8): Compile mature lesson communities into Claude Code skills
  - 3 new MCP tools: `compile_skill`, `list_compiled_skills`, `ungraduate_skill`
  - `skill_compiler.py` module with maturity assessment, community-to-skill compilation, graduation tracking, and drift detection
  - `mgcp-compile-skills` CLI with compile/list/status/ungraduate subcommands
  - `CompiledSkill` model for tracking compiled skill metadata
  - `graduated_to` field on Lesson model — graduated lessons are filtered from `query_lessons` but remain traversable via `spider_lessons`/`get_lesson`
  - 2 new REM operations: `skill_readiness` (detects compilation-ready communities) and `skill_drift_detection` (tracks post-compilation changes)
  - Skills written as `user-invocable: false` SKILL.md files to `~/.claude/skills/`

### Changed
- **Tool count**: 35 → 38 MCP tools

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

### Phase 8: Skill Compilation (Complete)
- Compile mature lesson communities into Claude Code skills (SKILL.md files)
- Lesson graduation tracking with `graduated_to` field
- Drift detection for compiled skills
- REM integration (skill readiness + drift detection operations)
- CLI and MCP tools for compilation management
