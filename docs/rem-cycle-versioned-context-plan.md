# REM: Recalculate Everything in Memory

## Versioned Context & Periodic Knowledge Consolidation

## Overview

MGCP currently clobbers project context on every save and overwrites lesson content on every refinement. This means:

- Session 46's context is gone once session 47 saves
- Lesson v1's action text is gone once v2 replaces it
- The only version history for lessons is the `[vN]` notes appended to the rationale field - the actual action text at each version is lost
- There's no mechanism to look back and see how a project evolved over time

This matters for two reasons:

1. **Training data pipeline** (Phase 7 finetuning brief) needs lesson version history to generate DPO preference pairs. Right now there's nothing to extract.
2. **Knowledge quality** degrades over time without periodic consolidation. Lessons accumulate, some go stale, duplicates creep in, and nobody runs community detection unless they remember to.

The REM cycle (by analogy with sleep's memory consolidation) addresses both: version everything, then periodically consolidate.

---

## What Changes

### 1. Context History Table

New table that captures a snapshot every time `save_project_context` runs.

```sql
CREATE TABLE IF NOT EXISTS context_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    session_number INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    notes TEXT,
    active_files JSON NOT NULL DEFAULT '[]',
    todos JSON NOT NULL DEFAULT '[]',
    recent_decisions JSON NOT NULL DEFAULT '[]',
    catalogue_hash TEXT,           -- SHA256 of catalogue JSON for change detection
    catalogue_delta JSON,          -- Only fields that changed since last snapshot
    FOREIGN KEY (project_id) REFERENCES project_contexts(project_id)
);

CREATE INDEX IF NOT EXISTS idx_context_history_project ON context_history(project_id);
CREATE INDEX IF NOT EXISTS idx_context_history_time ON context_history(timestamp);
```

**Why not store the full catalogue every time?** Catalogues are large (the Acme example is ~4KB of JSON). With 50+ sessions, that's a lot of redundant data. Instead, store a hash and a delta - only the fields that actually changed. The full catalogue lives in `project_contexts` as it does today. History lets you reconstruct the evolution without bloating the database.

**Why store notes/todos/decisions fully?** These are small and change frequently. A delta would be more complex than the data it saves. Just snapshot them.

### 2. Lesson Version History Table

New table that captures the full state of a lesson before each refinement.

```sql
CREATE TABLE IF NOT EXISTS lesson_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    action TEXT NOT NULL,
    rationale TEXT,
    tags JSON NOT NULL DEFAULT '[]',
    timestamp TEXT NOT NULL,
    refinement_reason TEXT,        -- Why the previous version was insufficient
    session_id TEXT,               -- Which session triggered this refinement
    FOREIGN KEY (lesson_id) REFERENCES lessons(id)
);

CREATE INDEX IF NOT EXISTS idx_lesson_versions_lesson ON lesson_versions(lesson_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lesson_versions_unique ON lesson_versions(lesson_id, version);
```

**When to capture:** Before `update_lesson` modifies the row, snapshot the current state into `lesson_versions`. The refinement_reason comes from the `refinement` parameter passed to `refine_lesson`.

**Backfill strategy:** For existing lessons with version > 1, we can partially reconstruct history from the `[vN]` annotations in the rationale field. Won't recover old action text, but preserves the refinement reasons. For v1 lessons, insert a single version record from current state.

### 3. REM Cycle: Periodic Consolidation

A new process that runs on a configurable schedule (default: every 10 sessions). It does four things:

#### 3a. Knowledge Extraction

Scan context history since last REM cycle:
- Extract recurring patterns from notes (themes that appear across 3+ sessions)
- Identify todos that were pending for 5+ sessions (stale work)
- Surface decisions that could become lessons (if they aren't already)

Output: A report of suggested lessons/refinements. Not auto-applied - the LLM or user reviews and accepts.

#### 3b. Community Detection

Run Louvain on the full lesson graph. Compare to last community run:
- New clusters that formed since last run
- Clusters that merged or split
- Orphan lessons (no relationships) that should be linked

Output: Updated community summaries. Auto-applied (community detection is idempotent).

#### 3c. Duplicate Detection

Run semantic similarity across all lessons (reuses `mgcp-duplicates` logic):
- Pairs above 0.90 threshold flagged for merge
- Near-duplicates (0.80-0.90) flagged for review

Output: Merge candidates. Not auto-applied.

#### 3d. Staleness Scan

Check lesson freshness:
- Lessons with 0 retrievals and created 30+ days ago
- Lessons with high retrieval count but last_refined 6+ months ago
- Lessons whose trigger keywords don't match any recent query (from telemetry)

Output: Staleness report. Not auto-applied.

### 4. REM MCP Tools

Three new tools:

```
rem_run           -- Trigger a REM cycle manually (runs all due operations, or specify which)
rem_report        -- View the last cycle's findings without running a new one
rem_status        -- Show schedule state: what ran when, what's due next
```

### 5. Cycle Scheduling

Not everything needs to run at the same frequency. Cheap operations can run often, expensive ones less so. Different operations get different schedules.

#### Schedule Strategies

```yaml
# ~/.mgcp/rem-config.yaml (or defaults in code)
rem:
  staleness_scan:
    strategy: linear
    interval: 5          # Every 5 sessions
    # Cheap: just checks timestamps and counts against thresholds.
    # Run often because stale lessons degrade retrieval quality fast.

  duplicate_detection:
    strategy: linear
    interval: 10         # Every 10 sessions
    # Medium cost: pairwise similarity on all lessons.
    # Duplicates accumulate slowly, no rush.

  community_detection:
    strategy: fibonacci
    intervals: [5, 8, 13, 21, 34, ...]  # Session counts that trigger
    # Expensive: Louvain on full graph + embedding for summaries.
    # Early on, clusters change fast as lessons are added. Later, the
    # graph stabilizes and communities shift slowly. Fibonacci matches
    # this natural decay in rate of change.

  knowledge_extraction:
    strategy: logarithmic
    base_interval: 10    # First run at session 10
    scale: 2.0           # ln(session_count / base) * scale
    # Extraction gets more valuable over time (more history to mine)
    # but the marginal value of each run decreases as the big patterns
    # get captured early. Log curve: runs close together initially
    # (lots of new signal), spreads out as the knowledge base matures.

  context_summary:
    strategy: linear
    interval: 20         # Every 20 sessions
    # Summarize context history into compressed narratives.
    # Only useful once you have enough history to compress.
```

#### Why Different Strategies?

| Strategy | Shape | Good For |
|----------|-------|----------|
| **Linear** | Every N sessions | Operations with constant cost and constant value per run |
| **Fibonacci** | 5, 8, 13, 21, 34... | Operations where value front-loads (early structure emerges fast, then stabilizes) |
| **Logarithmic** | Frequent early, sparse later | Operations where early signal is highest (first patterns are most impactful) |

The hook checks which operations are due at the current session count and only fires those. A session 13 might trigger staleness + community but not duplicates or extraction.

#### Implementation

Simple: store last-run session number per operation in a `rem_state` table. On session start, check each operation against its schedule. Return a list of what's due.

```sql
CREATE TABLE IF NOT EXISTS rem_state (
    operation TEXT PRIMARY KEY,
    last_run_session INTEGER NOT NULL DEFAULT 0,
    last_run_timestamp TEXT NOT NULL,
    last_run_result JSON,           -- Summary of findings
    next_due_session INTEGER        -- Pre-computed for quick checks
);
```

### 6. REM Cycle Hook

New hook: `rem-cycle-trigger.py`

Checks session count on `SessionStart`. Compares against `rem_state` to determine which operations are due. If any are due, injects a reminder:

> "REM cycle due at session N. Operations ready: staleness_scan, community_detection. Run rem_cycle_run to consolidate."

The hook doesn't auto-run the cycle. It reminds the LLM, which can then decide when to run it (beginning of session is ideal since it's low-context).

### 7. Interactive Review (Human-in-the-Loop)

The REM cycle shouldn't just dump a report and move on. Each finding should be presented as an interactive question that the human can approve, reject, or modify.

When the LLM runs `rem_run`, instead of silently applying changes or producing a wall-of-text report, it walks through findings one at a time using the `AskUserQuestion` flow:

#### Example: Duplicate Detection

```
Found potential duplicate lessons (similarity: 0.93):

1. "verify-method-exists" - BEFORE calling any method, verify it exists
2. "check-function-signature" - Always check function signatures before calling

What should we do?

[ ] Merge into one lesson (Recommended)
[ ] Keep both - they're different enough
[ ] Delete one (specify which)
[ ] Skip for now
```

#### Example: Staleness Scan

```
Lesson "chromadb-migration-steps" hasn't been retrieved in 45 days
and its trigger keywords don't match any recent queries.

MGCP migrated from ChromaDB to Qdrant in v1.1.0.

What should we do?

[ ] Archive it (remove from active retrieval, keep in history)
[ ] Delete it
[ ] Update trigger keywords
[ ] Keep as-is
```

#### Example: Knowledge Extraction

```
Across the last 8 sessions, your notes mention "connection pooling"
in 5 of them. There's no lesson covering this topic.

Should we create a lesson from these patterns?

[ ] Yes, draft a lesson for me to review
[ ] No, this is project-specific context (not reusable)
[ ] Skip for now
```

#### Example: Community Detection

```
Community detection found a new cluster of 4 lessons about
"error handling in webhooks":

- webhook-retry-logic
- stripe-signature-verify
- dead-letter-queue-replay
- webhook-timeout-handling

These aren't linked to each other yet.

[ ] Link them all (create "related" relationships)
[ ] Let me pick which ones to link
[ ] Skip - they're coincidentally similar
```

#### Implementation

The REM engine produces a list of `RemFinding` objects, each with:
- A human-readable description of what was found
- Proposed action(s) as selectable options
- A callback that executes the chosen action

The MCP tool (`rem_run`) returns findings one at a time. The LLM presents each to the user via `AskUserQuestion`, collects the response, and feeds it back. This keeps the human in the loop on every change while the system does the analytical heavy lifting.

For batch mode (headless/CI), add `--auto` flag that applies recommended actions without prompting. Useful for automated maintenance, but the default interactive mode is the primary UX.

---

## Migration Strategy

### For Existing Users

The migration must be **zero-effort for users** and **non-destructive**.

#### Step 1: Schema Migration (automatic on startup)

Add to `persistence.py` initialization (the `_ensure_schema` path):

```python
# Add context_history table if it doesn't exist
await conn.executescript("""
    CREATE TABLE IF NOT EXISTS context_history (...);
    CREATE TABLE IF NOT EXISTS lesson_versions (...);
    CREATE INDEX IF NOT EXISTS ...;
""")
```

`CREATE TABLE IF NOT EXISTS` is safe to run on every startup. No ALTER TABLE needed - these are new tables.

#### Step 2: Backfill Lesson Versions (one-time migration)

Add to `migrations.py` as Migration 5:

```python
async def backfill_lesson_versions(db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Create initial version records for all existing lessons.

    For lessons at v1: insert one record from current state.
    For lessons at v2+: insert current state as latest version,
    then attempt to parse [vN] annotations from rationale to
    reconstruct earlier refinement reasons (action text is lost).
    """
```

This runs in `run_all_migrations()` alongside existing migrations. It's idempotent - checks if `lesson_versions` already has records for each lesson before inserting.

#### Step 3: Backfill Context History (one-time migration)

Add to `migrations.py` as Migration 6:

```python
async def seed_context_history(db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Create one initial history record for each existing project context.

    This gives every project a "starting point" in history, even though
    we don't have their actual session-by-session evolution.
    """
```

Inserts the current state of each project as session 0. Future saves append real history.

#### Migration Safety

- All migrations check for pre-existing data before inserting (idempotent)
- New tables use `IF NOT EXISTS` (safe to re-run)
- No existing tables are altered or dropped
- No existing data is modified
- If migration fails partway, the next startup picks up where it left off

### For Fresh Installs

New tables are created by the existing `SCHEMA` constant in `persistence.py`. No migration needed - the tables are just there from the start.

---

## Implementation Order

### Phase A: Version Infrastructure (prerequisite for everything else)

1. Add `context_history` and `lesson_versions` tables to `SCHEMA`
2. Modify `save_project_context` to also insert into `context_history`
3. Modify `update_lesson` to snapshot into `lesson_versions` before overwriting
4. Add backfill migrations to `migrations.py`
5. Add retrieval methods: `get_context_history(project_id, limit)`, `get_lesson_versions(lesson_id)`
6. Tests for all of the above

This phase has **zero user-facing changes**. The system quietly starts recording history. Existing behavior is identical.

### Phase B: REM Engine & Scheduling

1. New module: `src/mgcp/rem_config.py` - schedule strategies (linear, fibonacci, logarithmic)
2. Add `rem_state` table to SCHEMA for tracking what ran when
3. New module: `src/mgcp/rem_cycle.py` - main engine that coordinates operations
4. Knowledge extraction from context history
5. Wire up existing community detection and duplicate detection
6. Staleness scan logic
7. `RemFinding` model with description, options, and action callbacks
8. Interactive review flow: findings returned one-at-a-time for LLM to present via AskUserQuestion
9. Batch mode (`--auto`) for headless execution
10. Tests for scheduling math + each operation + finding generation

### Phase C: MCP Tools & Hook

1. Add `rem_run`, `rem_report`, and `rem_status` tools to `server.py`
2. Add `rem-cycle-trigger.py` hook
3. Update `mgcp-init` to install the new hook
4. Update docs (README tool count, CLAUDE.md)

### Phase D: Finetuning Pipeline Integration

1. Update the finetuning brief to reference `lesson_versions` table
2. The `LessonExtractor` in the finetuning pipeline can now pull real version history instead of parsing rationale text
3. DPO pairs become trivial: for each lesson with 2+ versions, pair consecutive versions

---

## Files Modified

| File | Change |
|------|--------|
| `src/mgcp/persistence.py` | Add tables to SCHEMA (context_history, lesson_versions, rem_state), modify save/update methods, add history retrieval |
| `src/mgcp/server.py` | Add 3 new MCP tools (rem_run, rem_report, rem_status) |
| `src/mgcp/models.py` | Add ContextSnapshot, LessonVersion, RemFinding, RemCycleReport, RemScheduleConfig models |
| `src/mgcp/migrations.py` | Add migrations 5 (backfill lesson versions) and 6 (seed context history) |
| `src/mgcp/rem_cycle.py` | **New** - REM cycle engine with pluggable schedule strategies |
| `src/mgcp/rem_config.py` | **New** - Schedule strategy logic (linear, fibonacci, logarithmic) and config loading |
| `hooks/rem-cycle-trigger.py` | **New** - Session count trigger hook |
| `tests/test_versioning.py` | **New** - Tests for version history |
| `tests/test_rem_cycle.py` | **New** - Tests for REM cycle |
| `tests/test_rem_scheduling.py` | **New** - Tests for schedule strategies |
| `README.md` | Update tool count, add REM to concepts |
| `CLAUDE.md` | Update tool count, add new files to architecture |
| `CHANGELOG.md` | Add to [Unreleased] section |

## Risks

| Risk | Mitigation |
|------|------------|
| Database size growth from history | Catalogue uses delta storage, not full snapshots. Add configurable retention (default: keep all, power users can prune) |
| Migration fails on corrupted data | All migrations are idempotent with try/except per-record. Partial failures don't block the system |
| REM cycle takes too long on large datasets | Community detection and duplicate detection are already O(n^2) at worst. Add a timeout and progress reporting |
| Backfill can't recover old lesson actions | Accepted limitation. Document in migration output. Future versions are captured correctly going forward |

## Open Questions

1. **Catalogue delta format** - Store as JSON diff (RFC 6902 patches) or simpler key-level "changed fields" dict? RFC 6902 is precise but complex. A simple `{"added": {...}, "removed": {...}, "changed": {...}}` dict is probably enough.

2. **Per-project schedules** - Should schedule config be global or per-project? A project touched daily needs more frequent consolidation than one touched monthly. Probably start global with per-project overrides.

3. **Auto-apply threshold** - Community detection results are auto-applied. Should duplicate merges above 0.95 similarity also be auto-applied? Safer to require review, but that means the LLM has to act on the report.

4. **History retention** - Keep all history forever? Or add a configurable retention window (e.g., keep last 100 sessions, compress older ones into monthly summaries)?

5. **Config file format** - YAML config in `~/.mgcp/rem-config.yaml`? Or store in SQLite alongside everything else? YAML is human-editable, SQLite is programmatically consistent. Leaning YAML with SQLite defaults as fallback.
