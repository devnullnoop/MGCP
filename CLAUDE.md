# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MGCP** (Memory Graph Core Primitives) is a Python MCP server providing persistent, graph-based memory for LLM interactions. The system stores lessons learned during LLM sessions in a graph structure, allowing semantic querying without loading full context histories.

**Status**: Alpha/Research project. Package version 3.0.0 (`pyproject.toml`, `mgcp.__version__`); the hook/feature line is versioned separately and sits at v2.15 (`src/mgcp/hook_templates/VERSION`, which is authoritative; this line has three times been written stale in the same commit that bumped it), and v2.2 through v3.0 is released under CHANGELOG `[3.0.0]`. Phases 1-7 complete plus v3 multi-session (Qdrant server mode, compare-and-swap writes, shared embedding daemon). Embedded single-session remains the default and MGCP needs no server, daemon or container. Actively dogfooding. Phase 8's plan of moving lessons out of `query_lessons` into compiled skill prompts was dropped, because it made retrieval less reliable. Skill compilation itself ships (v2.3): it emits a SKILL.md file and never writes to the knowledge store.

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

# Run the shared embedding daemon (optional; the first client starts one if absent)
mgcp-embed
mgcp-embed --status   # is this machine sharing a model, or loading one per process?

# Multi-session: install and run a local Qdrant server (no container, nothing to fetch)
mgcp-qdrant setup     # download + verify + start + write qdrant_url to config.json
mgcp-qdrant status    # installed version, pid, answering, and which mode is live
mgcp-qdrant teardown  # stop it and return to embedded single-session

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

# Rebuild the Qdrant index from SQLite (lessons.db is the source of truth)
mgcp-migrate                         # Migrate data to Qdrant
mgcp-migrate --dry-run               # Preview what would be migrated
mgcp-migrate --force                 # Overwrite existing Qdrant data
```

## Architecture

The system flows from Claude/LLM through MCP Protocol to the MGCP Server, which contains Query Handler, Lesson Manager, and Graph Walker components. These connect to dual storage backends: Graph Store (NetworkX) and Vector Store (Qdrant).

### Core Components

All source files are in `src/mgcp/`:

- `server.py` - MCP server with 50 tools
- `models.py` - Pydantic models (Lesson, ProjectContext, ProjectCatalogue, SecurityNote, Convention, etc.)
- `graph.py` - NetworkX graph operations with typed relationships and Louvain community detection
- `embedding.py` - Centralized BGE embedding model (`BAAI/bge-base-en-v1.5`); daemon-first with an in-process fallback
- `embedding_daemon.py` - Shared embedding daemon (`mgcp-embed`): BGE loaded once per machine, served over a unix socket
- `qdrant_server.py` - Installs and supervises a local Qdrant server (`mgcp-qdrant`): platform-matched official binary, sha256-verified, loopback only, telemetry off
- `config.py` - Machine-local settings in `~/.mgcp/config.json`; holds `qdrant_url`, which is how multi-session reaches an MCP server whose environment the LLM client controls
- `qdrant_vector_store.py` - Qdrant integration for lesson, workflow, and community summary search
- `qdrant_catalogue_store.py` - Qdrant integration for project catalogue search
- `persistence.py` - SQLite/JSON storage for lessons, project contexts, and community summaries
- `telemetry.py` - Usage tracking and analytics
- `web_server.py` - FastAPI API + the instrument panel (8 analytics endpoints, one served app)
- `launcher.py` - Unified CLI launcher
- `bootstrap.py` - Initial lesson seeding
- `migration.py` - Rebuilds the Qdrant index from SQLite
- `init_project.py` - Multi-client MCP configuration (8 LLM clients supported)
- `data_ops.py` - Export, import, and duplicate detection
- `rem_cycle.py` - REM (Recalibrate Everything in Memory) cycle engine
- `rem_config.py` - REM scheduling strategies (linear, fibonacci, logarithmic)
- `backup.py` - Backup and restore functionality
- `bootstrap_loader.py` - Load bootstrap lessons, workflows, and relationships from YAML files
- `logging_config.py` - Centralized logging with rotation (10MB max, 5 backups)
- `reminder_state.py` - Self-directed reminder system for LLM workflow continuity
- `enforcement.py` - Enforcement rule schema, evaluator, and default rules (v2.4)
- `intent_config.py` - Intent routing config schema and DEFAULT_INTENTS (v2.2)
- `skill_compiler.py` - Compiles intent + workflow + lessons into SKILL.md (v2.3)

### Data Model

**Every agent-facing write is envelope-guarded.** `reject_tool_call_envelope` (`models.py`) runs on all six: `add_lesson`, `update_lesson`, `save_project_context`, `save_workflow`, `save_community_summary` and `write_soliloquy`. It refuses text that is a serialised tool-call envelope rather than prose. That corruption had already reached 7 of 24 stored project contexts before the guard existed, and which `get_project_context` still has to tolerate on read so those projects can resume.

**A file, not an environment variable, selects the shared store.**
`get_qdrant_url()` reads `MGCP_QDRANT_URL` first, then `qdrant_url` from
`~/.mgcp/config.json`. The file is what makes the feature usable. The LLM client starts the MCP
server, so `export` in a shell never reaches it, and sharing a store means several such
programs. `mgcp-qdrant setup` writes that key after it installs and starts the server. A session
that finds its configured server stopped will start it, and `MGCP_QDRANT_AUTOSTART=0` prevents
that. A failed start falls through to the per-store error described below instead of raising. A
config file that exists but does not parse raises an error. Quietly using the built-in index
while the operator believes they are on the server means two sessions writing to two stores with
nothing to show it. The server keeps its data in `~/.mgcp/qdrant-server`, which is not the
built-in directory.

**A locked search index does not stop unrelated tools.** `_ensure_initialized` opens SQLite,
the graph, and telemetry. `_ensure_vector_stores` opens Qdrant the first time one of the ten
tools that need it asks, and raises `VectorStoreUnavailableError` naming the process holding the
lock and the `mgcp-qdrant setup` remedy. The two used to share one `try` block, so a lock held by
another process failed all 50 tools. That included `read_soliloquy` and `get_project_context`,
which need no search at all, so a session that started while a previous session's server was
still running could neither load its memory nor save it. Writes now continue instead of failing,
because `add_lesson` is the only exit from the apology gate, and `_ensure_vector_stores` adds
whatever was written while the index was locked. `delete_lesson` and `remove_catalogue_item`
still refuse, because a search entry that outlives its deleted record returns a result for
something that no longer exists.

**Injected text carries a relative age, not a date.** `relative_age` and `age_phrase` in
`models.py` turn a timestamp into "now", "5h", "3d", "7mo" or "2y". Four surfaces use them:
each result from `query_lessons` shows the age of its current wording from `last_refined`, the
project header shows how long since the project was last touched, each active todo shows how
long it has been pending, and the journal header shows the interval beside its absolute stamp.
The reader is poor at date arithmetic, so the arithmetic happens before the text reaches it. The
cost is about 29 tokens on a 2,200-token `query_lessons` response. A separate measurement
([locomo-retrieval-eval.md](docs/locomo-retrieval-eval.md)) found that putting a date in the
*embedded* text does not help retrieval, so this is injected text only and the index is
unchanged.

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

### MCP Tools (50 total)

**Lesson Discovery & Retrieval (5):**
- `query_lessons` - Semantic search for relevant lessons
- `get_lesson` - Get full lesson details by ID
- `spider_lessons` - Traverse related lessons from a starting point
- `list_categories` - Browse top-level lesson categories
- `get_lessons_by_category` - Get lessons under a category

**Lesson Management (4):**
- `add_lesson` - Create a new lesson
- `refine_lesson` - Improve an existing lesson. `new_trigger` replaces the trigger, which is the field retrieval matches on, so a lesson nobody can find can be corrected rather than only added to. `new_tags` replaces the tag list, where omitting it keeps the tags and an empty list removes them all. Both old values are kept in the version history. The call rewrites SQLite, the search index, and the in-memory graph node, which until now kept the pre-refinement copy for the rest of the session.
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

**REM opens the vector store only when it needs one.** Six of the seven operations need no vectors. `rem_run` hands the engine a factory rather than an open store, and `_duplicate_detection` awaits it. Opening one up front took the Qdrant lock on every cycle, including the cycles `rem-required-before-commit` forces when nothing is due, so a session that never searched anything held the lock for the rest of its life and blocked the dashboard and any second session. A failed open leaves the other six operations running.

**REM Cycle (3):** All three take an optional `project_path` (empty = `CLAUDE_PROJECT_DIR`, else cwd) and report an error rather than a guess when that project has no saved context.
- `rem_run` - Run consolidation operations (staleness, duplicates, communities)
- `rem_report` - Per-operation last run, next due and finding count for this project. Findings themselves are not persisted. Only `{"finding_count": N}` reaches `rem_state`
- `rem_status` - Show schedule state and what's due for this project

**The REM cadence is per project; the corpus is global.** A cycle triggered from
any project maintains the whole shared knowledge store, but *when* it is due
follows that project's own session count. Both halves of that have to be per
project, and for a long time only one was: `server.py` fed the scheduler
`max(session_count)` across every project (fixed in v2.5), while `rem_state`
stayed keyed `operation TEXT PRIMARY KEY`, which is one row for the entire machine. Since
`is_due()` opens by refusing anything `<= last_run_session`, this repo's own
session 98 was written into the single shared row and every younger project
inherited it; BoltMob at session 36 could not become due until it reached 100.
`rem_state` is now keyed `PRIMARY KEY (project_id, operation)`, and
`get_rem_state`/`update_rem_state` take a required `project_id` with no default.
A caller that forgets it is the bug the key exists to prevent.

Opening a store written before this change rebuilds the table automatically and
idempotently (`LessonStore._run_migrations`). The pre-existing global rows are
attributed to the project with the highest `session_count`, which is
whose clock the old `max()` scheduler was reading, and logged at WARNING. Every
other project starts with no row, which is the truth: REM has never been
scheduled on its own clock there, so everything is immediately due.

**The published due date is derived from `is_due`, not computed alongside it.**
`next_due_session(schedule, last_run_session)` returns the first session at which
`is_due` fires, by asking it. It used to do its own arithmetic, the next multiple
of the interval, which is a different rule from "sessions elapsed since the last
run": `staleness_scan` (linear, 5) last run at 98 was published as due at 100 and
did not actually fire until 103. That number is not cosmetic; the SessionStart
detector reads `rem_state.next_due_session` out of the table and warns "REM
Operations Overdue" when `session_count` reaches it, so early dates produced
warnings for operations that were not due. Rows written by the old arithmetic,
and rows left behind by operations that no longer exist, which the detector would
otherwise report overdue by name, are repaired on every store open
(`persistence.repair_rem_state`).

**Workflow State (1):**
- `update_workflow_state` - Update active workflow, current step, and completion status. Workflow and step IDs are checked against the stored workflow first, because the hook replays `current_step` every turn as "EXECUTE step '<id>' now": an ID no workflow contains repeats an instruction `get_workflow_step` can only answer "not found" to, for as long as the state says so. The write is the only place that can be caught. A call whose every argument is empty or false now reports that it changed nothing, rather than answering "updated" after writing nothing.

**Reminder Control (2):**
- `schedule_reminder` - Schedule self-directed reminders for workflow continuity
- `reset_reminder_state` - Reset reminder state to defaults

**Soliloquy (2):**
- `write_soliloquy` - Write a reflective message to your future self (at session close/compression)
- `read_soliloquy` - Read your most recent message(s) to yourself (at session start)

The journal is **stored globally** as one continuous inner voice rather than one
per codebase, and **read project-aware**. `read_soliloquy` returns this
project's most recent entry, and only when this project has none does it fall
back to the newest entry from anywhere, labelled with where it came from. Both
tools resolve the project from `project_path`, else `CLAUDE_PROJECT_DIR`, else
cwd. Entries written before tagging existed carry no project tag and read as
"from an untagged earlier session".

**Intent Config (6):**
- `list_intents` - List all configured intents (name, description, action, tag/keyword counts)
- `get_intent` - Get full definition of one intent by name
- `add_intent` - Add a new intent to the routing config (closes the REM growth loop)
- `update_intent` - Update an existing intent's description/action/tags/linked_workflow/keyword_patterns
- `remove_intent` - Delete an intent from the routing config
- `compile_intent_to_skill` - Compile an intent (+ linked workflow + lessons) into a SKILL.md file at `~/.claude/skills/` or `<project>/.claude/skills/`. Walks intent → workflow → ordered steps → lessons-per-step. Skill is a downstream artifact; the intent stays the source of truth.

**Gate Adjudication (1):** The apology gate's second exit (v2.11).
- `adjudicate_apology_gate` - Contest or confirm a gate fire on the record: flagged sentence + verdict + reasoning (>=20 chars) appended to `~/.mgcp/gate_audit.jsonl`; verdict `not_apology` opens the gate for the current turn, `apology` keeps it shut until `add_lesson`

**Enforcement Rules (6):** Data-driven PreToolUse gates stored in `~/.mgcp/enforcement_rules.json`. Edits take effect on the next tool call.
- `list_enforcement_rules` - List all rules with enabled/disabled status and trigger
- `get_enforcement_rule` - Full JSON definition of one rule
- `add_enforcement_rule` - Add a rule: `trigger` dict, `preconditions` list, `bypass_scope`, `deny_reason`
- `update_enforcement_rule` - Change fields on an existing rule
- `remove_enforcement_rule` - Delete a rule by name
- `toggle_enforcement_rule` - Enable/disable without deleting

## Web UI

`python -m mgcp.web_server` serves an instrument panel at `/`. One document, eight views, routed
client-side on the hash. It replaced eight separate pages in v2.13; those pages answered "what is
stored" and covered none of enforcement, REM, the gate audit or the journal.

| View | Question it answers |
|------|---------------------|
| Signal | Is the memory working? Match quality over time, and the matched-vs-bridged split |
| Retrieval | Which queries failed to surface anything good. The miss log, with an adjustable threshold |
| Effectiveness | Which lessons earn their place: matched count against mean score when matched |
| Enforcement | What the gates did: denials, capture rate, per-rule fires, every contested fire |
| Graph | Lessons, categories, workflows and steps, and which lessons carry no edges |
| REM | What maintenance is due, per project, on that project's own clock |
| Journal | The soliloquy record. No other view shows it |
| Curate | Edit and delete lessons, compile intents to skills |

Backed by eight read-only analytics endpoints in `web_server.py` (`/api/signal`,
`/api/retrieval/*`, `/api/effectiveness`, `/api/gate-audit`, `/api/enforcement/rules`,
`/api/rem/state`, `/api/soliloquies`). The views follow three rules, and all three change what you
see: a slot logged with score `0.0` was **appended by the community bridge, not matched**,
and averaging the two together is what made a good lesson read as 2% relevant; status is shown
as a glyph plus a word, never hue alone, because `good` and `critical` measure a CVD ΔE of 4.1;
and **every count has a per-distinct-question twin**, because the hooks issue some of the queries
they are being measured by.

`/api/signal` returns a `concentration` block beside `retrieval`, and the Signal view leads with
the deduplicated figure while keeping the raw one as its sub-label. The git gate mandates
`query_lessons('git commit')` before any commit, so that one string is 521 of 1,161 recorded
queries in the operator's own store and one lesson wins 517 of them at a nearly constant score.
Read as a raw count that is nine months of varied recall; it is one reflex. It moved the published
median match from 0.608 to 0.691. Neither number is suppressed, because the repeated query really
is asked that often and really does matter each time. The gap between them is the share of the
evidence the system generated for itself, and no single number shows it.

Buildless: Tailwind-free CSS, d3 from a CDN, no npm and no build step. Assets live in
`src/mgcp/static/app/`.

## Claude Code Integration

Add to Claude Code MCP config (`~/.claude.json`):

```json
{
  "mcpServers": {
    "mgcp": {
      "command": "python",
      "args": ["-m", "mgcp.server"]
    }
  }
}
```

Data is stored in `~/.mgcp/` by default.

## Claude Code Hooks

MGCP v2.2 makes the routing prompt **data, not code**. The intent classification system has 8 intent categories (`git_operation`, `catalogue_dependency`, `catalogue_security`, `catalogue_decision`, `catalogue_arch_note`, `catalogue_convention`, `task_start`, `session_end`) defined in `~/.mgcp/intent_config.json`. Both Claude Code hooks and the REM intent_calibration operation read from this file. Adding or modifying an intent no longer requires editing hook code or shipping a release. You edit the JSON (or call `add_intent` / `update_intent` MCP tools, or let REM propose the change) and the next session picks it up automatically.

| Hook | Event | Type | Purpose |
|------|-------|------|---------|
| `session-init.py` | SessionStart | advisory | Injects the bootstrap checklist (`read_soliloquy` / `get_project_context` / `query_lessons`) and workflow execution discipline. Detects three things and reports them: stale `.py` hook references in settings.json, REM operations past their `next_due_session`, and a high apology-gate contest rate counted from `gate_audit.jsonl`. |
| `user-prompt-dispatcher.py` | UserPromptSubmit | advisory | Puts a clock on the first line of every turn (`<time>⌚ 21:09 Fri 2 Oct · 6m since your last message · 48m into this session</time>`), built before anything else and unable to fail the rest of the block. Then applies hard keyword gates loaded from `intent_config.json` (git and session_end fire from one loop), re-injects the terse routing block, and delivers scheduled reminders. Resets per-turn state: clears `turn_tools_called` and any prior adjudication, records `turn_session_id`, and parses `MGCP_BYPASS[:scope]` into `turn_bypass_scopes`. |
| `pre-tool-dispatcher.py` | PreToolUse | **enforcing** | The only hook that can refuse a tool call. Two mechanisms: a generic evaluator over `~/.mgcp/enforcement_rules.json`, and a built-in apology gate whose trigger is assistant text rather than a tool argument. While the gate is armed only `add_lesson`, `adjudicate_apology_gate` and the three discovery tools are permitted. Gating discovery would gate the exits themselves. Fails open on any parse error. Bash commands are tokenised per line with `shlex(punctuation_chars=True)`, git's global flags are skipped so `git -C /path commit` is still a commit. An untokenisable line falls back to a raw boundary scan that **fails closed for git only**. Scoped bypass: `MGCP_BYPASS:<scope>` disables one scope, bare `MGCP_BYPASS` disables all. Every apology-gate refusal and compliance records the matched pattern and the flagged sentence (v2.15); until then only a contested fire carried the sentence, so the agent chose the evidence for every record that had any. The apology window stops at `turn_started_at` as well as at a user entry, because a message sent mid-turn fires UserPromptSubmit (which clears `turn_tools_called`) but reaches the transcript as `queue-operation` and `attachment` entries, never as `type == "user"`. The walk sailed past it and kept reading the previous turn, so an apology already answered with a lesson re-armed the gate. See `docs/mgcp-interception-flow.html` for the decision diagram. |
| `post-tool-dispatcher.py` | PostToolUse | advisory | Appends every tool name to `turn_tools_called`, which PreToolUse preconditions read. Edit/Write triggers a knowledge-capture checkpoint; Bash output is scanned for error patterns with a cooldown, over the whole serialised `tool_response` so stderr is included. |
| `mgcp-precompact.py` | PreCompact | advisory | Critical reminder to save context (and write_soliloquy) before context compression |

The dispatcher falls back to a minimal hard-coded intent set if the JSON file is missing or corrupt, so a fresh install never crashes. The PreToolUse hook allows the tool call on any parse error, because enforcement is a net rather than a tripwire. The MCP tools deliberately do **not** match that: a *missing* `enforcement_rules.json` yields the built-in defaults (a fresh install has no other truth), but a file that exists and does not parse now raises, and the calling tool reports the parse error. Falling back to defaults there was worse than useless. The tool would load defaults, apply the caller's edit and save, silently overwriting whatever the user had written in the file the hook is still enforcing from. Legacy regex hooks (`git-reminder.py`, `catalogue-reminder.py`, `task-start-reminder.py`) are archived in `examples/claude-hooks/legacy/`.

**Advisory vs. enforcing.** The first four hooks inject text into `<system-reminder>` tags that the LLM may skim or ignore. `pre-tool-dispatcher.py` is different: it returns `permissionDecision: "deny"` with a `reason` string and the Claude Code harness refuses to run the tool. This addresses the repeated failure mode where `query-before-git-operations` was violated (v1→v4) despite correct hook fires. See `docs/mgcp-interception-flow.html` for the full interception map and remaining enforcement gaps.

**Growth loop:** REM intent_calibration runs community detection on the lesson graph and surfaces findings when (a) a community has unmapped tags or (b) a community spans multiple intents with no clear dominant share below 60%. That second check catches misfit clusters that the v2.1 hand-coded `tag_to_intent` map silenced via defensive over-mapping. Findings include structured `proposed_patch` metadata so the LLM (or a future automated writeback path) can call `add_intent`/`update_intent` directly. Lesson community → REM finding → intent_config update → next session's hook injection picks up the new intent. No code commit required.

**Compiling an intent into a skill (v2.3):** An intent + its linked workflow + the workflow's per-step lessons can be compiled into an Anthropic-format SKILL.md file at `~/.claude/skills/{intent_name}/SKILL.md` (user scope) or `<project>/.claude/skills/{intent_name}/SKILL.md` (project scope). The compiler walks intent → workflow → ordered steps → lessons-per-step and inlines all four layers into a single self-contained document. Compiled skills give intents two new firing channels, slash commands such as `/git_operation` and Claude's own discovery by description, on top of MGCP's hook-level keyword gates and LLM intent classification.

**Critical anti-Phase-8 invariant:** compiling a skill is only additive. It does NOT remove the source intent from `intent_config.json`, it does NOT remove backing lessons from the active query pool, and it does NOT change any MGCP behavior. The intent stays the source of truth, and the skill is a copy you can rebuild at any time. Phase 8 did the opposite. It hid compiled lessons from `query_lessons`, and that made retrieval less reliable. Compilation is reachable from the instrument panel's Curate view, which posts to `/api/intent-config/intents/{name}/compile`, which calls the same function as the MCP tool.

**Enforcement-as-data (v2.4):** The PreToolUse hook is a generic evaluator. Rules live in `~/.mgcp/enforcement_rules.json` with this shape:

```jsonc
{
  "version": 1,
  "rules": [
    {
      "name": "git-requires-query-lessons",
      "enabled": true,
      "trigger": {"tool_name": "Bash",
                  "command_match": {"type": "git_subcommand",
                                    "subcommands": ["commit", "push"]}},
      "preconditions": [
        {"type": "tool_called_this_turn",
         "tool_name": "mcp__mgcp__query_lessons"}
      ],
      "bypass_scope": "git",
      "deny_reason": "git commit/push requires query_lessons first"
    }
  ]
}
```

Trigger `command_match.type` ∈ {`git_subcommand`, `regex`, `contains`}. Precondition `type` ∈ {`tool_called_this_turn`, `tool_not_called_this_turn`, `staged_files_coupling`, `tool_input_glob`}. The staged-file coupling type takes `couplings: [{"when_staged": [glob,...], "require_one_of": [glob,...]}]`. If any staged file matches `when_staged`, at least one must match `require_one_of` or the tool call is denied. Use it to enforce doc-coupling, test-coupling, or changelog discipline on commits. The `tool_input_glob` type takes `field` (which `tool_input` key to read) and `deny_globs`, and denies when any glob matches that field. Use it to gate Edit/Write against sensitive paths (settings.json, secrets) or to gate URL targets on web fetches. It fails open on a missing field or a non-string value.

**Committed prose has a style, and a rule that checks one part of it.** Everything written
into this repository follows ASD-STE100 and the Google developer documentation style guide: one
idea per sentence, 20 words or fewer, active voice, present tense, and every technical term
defined where it first appears. This covers commit messages, README, CHANGELOG, documents under
`docs/`, code comments, and docstrings. The lesson `human-prose-no-ai-tells` carries the full
rule and surfaces on `query_lessons("git commit")`, which the git gate already forces. The
seeded rule `commit-message-prose-style` refuses any commit whose message contains an em dash.
That check is one character on purpose, because a wider pattern would refuse legitimate commits.
Bypass with `MGCP_BYPASS:prose` when a message quotes an em dash to describe the rule itself.

Per-turn state flows through `workflow_state.json`: UserPromptSubmit resets `turn_tools_called=[]` and parses `MGCP_BYPASS[:scope]` tokens into `turn_bypass_scopes`. PostToolUse appends every tool name to `turn_tools_called`. PreToolUse reads both. Schema + defaults live in `src/mgcp/enforcement.py`; **the evaluator lives in the hook and only in the hook**. The hook must be stdlib-only with no `mgcp` import, and a second copy in the package had no production caller. `server.py` imports the models and `load_config`/`save_config`, never the evaluator. That copy, and the ~300 lines of tests that were its only consumer, were deleted; the evaluator's contract is tested where the code runs, in `tests/test_pre_tool_dispatcher.py`. `tests/test_enforcement.py` covers the schema and its round-trip.

## Implementation Roadmap

1. ~~Phase 1: Basic lesson storage and retrieval via MCP~~ Complete
2. ~~Phase 2: Semantic search with embeddings~~ Complete
3. ~~Phase 3: Graph traversal and hierarchical structure~~ Complete
4. ~~Phase 4: Refinement, versioning, and learning loops~~ Complete
5. ~~Phase 5: Quality of Life~~ Complete - Multi-client support, export/import, backup/restore, proactive hooks
6. ~~Phase 6: Proactive Intelligence~~ Complete - Intent-based LLM self-routing, REM intent calibration, workflow state management
7. ~~Phase 7: Feedback Loops~~ Complete - REM cycle engine (staleness scan, duplicate detection, community detection, knowledge extraction), versioned context history, lesson version snapshots, scheduled reminders
8. Phase 8, skill compilation. **The plan was dropped and the feature shipped.** The abandoned strategy was replacing stored knowledge with compiled skill prompts: graduated lessons were hidden from `query_lessons`, which degraded reliability. Hook-based injection outperforms skill files, so lessons stay in the active query pool. The compiler itself is live as v2.3 `compile_intent_to_skill`. It emits a SKILL.md file and never touches the knowledge store. REM owns knowledge maintenance; skill compilation owns file emission.
