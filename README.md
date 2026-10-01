# Memory Graph Core Primitives (MGCP)

**Persistent context for stateless LLMs.**

[![License](https://img.shields.io/badge/License-O'Saasy-blue.svg)](https://osaasy.dev/)
[![Python](https://img.shields.io/badge/Python-3.11%20|%203.12-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

> **Alpha Software** - Actively dogfooding as we build. Working, but APIs may change.

**Current release: 3.0 — [more than one session at a time](#what-30-adds-more-than-one-session-at-a-time).**
Earlier releases are in [CHANGELOG.md](CHANGELOG.md); this README describes the system as it is now.

## The Problem

LLMs are stateless. Every session starts from zero. The AI that helped you debug authentication yesterday has no memory of it today. Lessons learned, project context, architectural decisions are all gone the moment the session ends.

You've seen it and lived it... explaining the same codebase structure over and over, watching the AI repeat a mistake you corrected last week, and then losing important context when a session ends.

## What MGCP Does

MGCP gives your LLM **persistent context that survives session boundaries**.

```
Session 1: LLM encounters a bug -> adds lesson -> stored in database

Session 2: LLM has no memory of Session 1
         -> Hook fires: "query lessons before coding"
         -> Semantic search returns relevant lesson
         -> Bug avoided
```

**The primary audience is the LLM, not you.** You configure the system and the LLM reads from, and writes to, it. The knowledge persists even though the LLM doesn't.

### What makes this useful:

- **Semantic search** finds relevant lessons without exact keyword matches
- **Graph relationships** surface connected knowledge together
- **Workflows** sequence multi-step processes and surface the right lessons at each step — guidance, not a gate
- **Hooks** make it proactive - reminders fire automatically at key moments
- **Project isolation** keeps context separate per codebase

### What this is NOT:

- Not "AI that learns" - lessons are added explicitly
- Not self-improving in the strong sense - the system never authors or rewrites its own knowledge. One loop IS automatic: the apology gate detects acknowledged failure in the assistant's own words and refuses every tool call until a lesson is written. Capture is forced; content is still authored.
- Not magic - it's structured context injection with good tooling

**Honest framing:** This is a persistent knowledge store with semantic search, workflow orchestration, and proactive reminders. The value is *continuity* - accumulated guidance that shapes LLM behavior across sessions.

## Real Value Delivered

In active use, MGCP has:

- **Caught bugs before they happened** - lessons from past mistakes surface before repeating them
- **Kept documentation in sync** - an enforcement rule refuses commits that change source without touching docs
- **Maintained project context** - picking up exactly where the last session left off
- **Enforced quality gates** - PreToolUse rules deny the tool call outright, which is the one instruction an LLM cannot skim past
- **Preserved architectural decisions** - rationale survives session boundaries

The system isn't intelligent. But an LLM with accumulated context *behaves* more intelligently than one starting fresh every time.

**On workflows specifically:** earlier versions of this list credited workflows with *enforcing* quality gates and *ensuring* steps are not shortcut. They do neither. `update_workflow_state` records progress and nothing reads a checklist to block on it — a checklist the LLM can skip is advice. Every gate in MGCP that actually holds is an enforcement rule, and crediting workflows for that hid the one genuinely novel thing in the system. Rows E06 and E07 in [docs/CAPABILITIES.md](docs/CAPABILITIES.md) record the retraction.

## How It Works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/architecture-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/architecture-light.png">
  <img alt="MGCP Architecture" src="docs/screenshots/architecture-dark.png" width="700">
</picture>

| Component | Purpose |
|-----------|---------|
| **SQLite** | Lessons, project contexts, telemetry |
| **Qdrant** | Vector embeddings for semantic search |
| **NetworkX** | In-memory graph for relationship traversal |
| **MCP Protocol** | Native integration with LLM clients |
| **Hooks** | Proactive reminders at key moments |

## Key Concepts

### Lessons
Knowledge with triggers and actions:
```
id: "verify-method-exists"
trigger: "api endpoint, database call, store method"
action: "BEFORE calling any method, verify it exists in the class"
rationale: "We once shipped code calling store._get_conn() which didn't exist"
```

When the LLM queries "working on api endpoint", this lesson surfaces.

### Workflows
Step-by-step processes with linked lessons:
```
workflow: api-endpoint-development
steps:
  1. Design -> linked lessons: [api-contract, error-responses]
  2. Implement -> linked lessons: [verify-method-exists]
  3. Test -> linked lessons: [manual-ui-test-required]
  4. Document -> linked lessons: [update-openapi]
```

Each step surfaces relevant guidance. Checklists prevent skipping.

### Project Context
Per-project state that persists:
- **Todos** with status (pending/in_progress/completed/blocked)
- **Decisions** with rationale (why we chose X over Y)
- **Catalogue** (architecture notes, security concerns, conventions)
- **Active files** being worked on

Session 47 knows what Session 46 was doing.

### Reminders
Self-directed prompts for multi-step work:
```python
schedule_reminder(
    after_calls=2,
    message="EXECUTE the Test step before responding",
    workflow_step="api-endpoint-development/test"
)
```

The LLM reminds itself to not skip steps.

## Screenshots

The dashboard is one instrument panel: a single document, eight views, routed client-side on the
hash. It replaced eight separate pages that each answered "what is stored" and covered none of
enforcement, REM scheduling, the gate audit or the journal.

> Every image below is rendered from a **synthetic seed store**, never from a real one. A live
> MGCP store holds query text, absolute paths from unrelated repositories, gate-audit transcripts
> and the soliloquy journal, so a screenshot of it is a publication of someone's work.

### Signal — is the memory working?
Match quality over time, and the split between results matched by relevance and results appended
by the community bridge. A slot logged with score `0.0` was appended, not matched; keeping the two
apart is the difference between a meaningful mean and a meaningless one.
![Signal](docs/screenshots/instrument-signal.png)

### Effectiveness — which lessons earn their place?
Every lesson placed by how often relevance matched it against how well it scored when it did.
Dot size is total appearances. Lessons never matched have no score, so they are counted and listed
rather than plotted at a false origin.
![Effectiveness](docs/screenshots/instrument-effectiveness.png)

### Enforcement — is the gate real or theatre?
Denials against lessons actually written, which rules fire, which have never fired, and every
contested fire with the sentence that triggered it beside the reasoning given.
![Enforcement](docs/screenshots/instrument-enforcement.png)

### REM — what maintenance is actually due?
Per project, per operation, on that project's own session clock. Overdue and never-run states are
named, not just coloured.
![REM](docs/screenshots/instrument-rem.png)

### Graph — what shape is the knowledge?
Lessons, the categories they hang under, workflows and their steps. Only nodes carrying an edge
appear, which is itself diagnostic.
![Graph](docs/screenshots/instrument-graph.png)

## Quick Start

### 1. Install

**Requires Python 3.11 or 3.12.** Python 3.13 is not supported (PyTorch has no wheels for it on Intel Macs).

```bash
git clone https://github.com/devnullnoop/MGCP.git
cd MGCP
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure Your LLM Client

```bash
mgcp-init
```

This will:
- Auto-detect installed LLM clients and configure the MCP server
- Deploy global hooks (Claude Code) for proactive reminders
- **Download the embedding model (~415MB) on first run** — this powers semantic search and only needs to happen once. Subsequent runs are instant.

Supports: Claude Code, Claude Desktop, Cursor, Windsurf, Zed, Continue, Cline, Sourcegraph Cody

### 3. Start Using

Restart your LLM client. MGCP tools are now available.

```bash
# Optional: seed starter lessons and workflows
mgcp-bootstrap

# Optional: start the web dashboard
mgcp-dashboard
```

## MCP Tools (50 total)

### Lesson Discovery (5)
| Tool | Purpose |
|------|---------|
| `query_lessons` | Semantic search for relevant lessons |
| `get_lesson` | Get full lesson details |
| `spider_lessons` | Traverse related lessons |
| `list_categories` | Browse lesson hierarchy |
| `get_lessons_by_category` | Get lessons in a category |

### Lesson Management (4)
| Tool | Purpose |
|------|---------|
| `add_lesson` | Create a new lesson |
| `refine_lesson` | Improve existing lesson |
| `link_lessons` | Create typed relationships |
| `delete_lesson` | Remove lesson from all stores |

### Project Context (5)
| Tool | Purpose |
|------|---------|
| `get_project_context` | Load saved context |
| `save_project_context` | Persist for next session |
| `add_project_todo` | Add todo item |
| `update_project_todo` | Update todo status |
| `list_projects` | List all projects |

### Project Catalogue (4)
| Tool | Purpose |
|------|---------|
| `search_catalogue` | Semantic search catalogue |
| `add_catalogue_item` | Add any item (arch, security, library, convention, coupling, decision, error, or custom) |
| `remove_catalogue_item` | Remove item |
| `get_catalogue_item` | Get item details |

### Workflows (8)
| Tool | Purpose |
|------|---------|
| `list_workflows` | List available workflows |
| `query_workflows` | Match task to workflow |
| `get_workflow` | Get workflow with steps |
| `get_workflow_step` | Get step with lessons |
| `create_workflow` | Create workflow |
| `update_workflow` | Update workflow |
| `add_workflow_step` | Add step |
| `link_lesson_to_workflow_step` | Link lesson to step |

### Community Detection (3)
| Tool | Purpose |
|------|---------|
| `detect_communities` | Auto-detect topic clusters via Louvain |
| `save_community_summary` | Persist LLM-generated community summary |
| `search_communities` | Semantic search community summaries |

### REM Cycle (3)

Knowledge stores rot. Lessons go stale, duplicates accumulate, and topic clusters shift as a project evolves. REM (Recalibrate Everything in Memory) runs periodic consolidation to keep the knowledge base healthy without manual curation.

Each operation runs on its own schedule — staleness scans every 5 sessions, duplicate detection every 10, community detection on fibonacci intervals (5, 8, 13, 21...), knowledge extraction on a logarithmic curve that starts frequent and slows down as the project matures. The schedules are configurable but the defaults work well in practice.

| Tool | Purpose |
|------|---------|
| `rem_run` | Run consolidation cycle (staleness, duplicates, communities) |
| `rem_report` | Per-operation last run, next due, and finding count |
| `rem_status` | Show schedule state and what's due |

### Workflow State (1)
| Tool | Purpose |
|------|---------|
| `update_workflow_state` | Track active workflow and step progress |

### Reminders (2)
| Tool | Purpose |
|------|---------|
| `schedule_reminder` | Schedule self-reminder |
| `reset_reminder_state` | Clear reminders |

### Enforcement Rules (6)
Data-driven gates for the PreToolUse hook. Edits take effect on the next tool call.
| Tool | Purpose |
|------|---------|
| `list_enforcement_rules` | List all configured rules |
| `get_enforcement_rule` | Full definition of one rule |
| `add_enforcement_rule` | Add a new rule (trigger + preconditions + bypass scope + deny reason) |
| `update_enforcement_rule` | Change fields on an existing rule |
| `remove_enforcement_rule` | Delete a rule |
| `toggle_enforcement_rule` | Enable/disable without deleting |

### Gate Adjudication (1)
The apology gate's second exit: contest a tripwire fire on the record instead of being hard-blocked.
| Tool | Purpose |
|------|---------|
| `adjudicate_apology_gate` | Record a verdict (`not_apology` opens the gate for the turn; `apology` keeps it shut until `add_lesson`) with mandatory reasoning, appended to the audit log |

### Intent Config (6)
The routing prompt is data. These tools edit `~/.mgcp/intent_config.json` from chat; the next session's hook injection picks up the change.
| Tool | Purpose |
|------|---------|
| `list_intents` | List configured intents (name, description, action, tag/keyword counts) |
| `get_intent` | Full definition of one intent |
| `add_intent` | Add a new intent |
| `update_intent` | Change fields on an existing intent |
| `remove_intent` | Delete an intent |
| `compile_intent_to_skill` | Emit an intent + its workflow + per-step lessons as a SKILL.md **file**. Purely additive — writes nothing back to the knowledge store. |

### Soliloquy (2)
The agent's message to its future self. Stored **globally** — one continuous inner voice, not one journal per codebase — but **read project-aware**: `read_soliloquy` returns this project's most recent entry, and only falls back to the newest entry from anywhere when this project has none, labelled with where it came from.
| Tool | Purpose |
|------|---------|
| `write_soliloquy` | Write a reflection for next-you (session close / compaction) |
| `read_soliloquy` | Read your most recent message(s) to yourself (session start) |

## Claude Code Hooks

Five hooks ship with MGCP. Four are **advisory**: they inject text into
`<system-reminder>` tags the LLM can read, skim, or ignore. One is **enforcing** —
`pre-tool-dispatcher.py` returns `permissionDecision: "deny"` and the harness refuses to
run the tool.

That distinction is the hardest-won result in the project. The `query-before-git-operations`
lesson was violated across four successive revisions while the advisory hook fired correctly
every single time. Interception was never compliance. Every gate in MGCP that actually holds
is a PreToolUse denial; everything else is advice.

| Hook | Event | Type | Purpose |
|------|-------|------|---------|
| `session-init.py` | SessionStart | advisory | Injects the bootstrap checklist (`read_soliloquy` / `get_project_context` / `query_lessons`) and workflow execution discipline. Detects and reports three things: stale `.py` hook references in `settings.json`, REM operations past their due session, and a high apology-gate contest rate. |
| `user-prompt-dispatcher.py` | UserPromptSubmit | advisory | Hard keyword gates loaded from `intent_config.json`, terse routing re-injection every message, scheduled reminders, and the per-turn state reset (`turn_tools_called`, `turn_session_id`, `MGCP_BYPASS[:scope]`). |
| `pre-tool-dispatcher.py` | PreToolUse | **enforcing** | The only hook that can refuse a tool call: a generic evaluator over `~/.mgcp/enforcement_rules.json` plus the built-in apology gate. Fails open on any parse error — enforcement is a net, not a tripwire. |
| `post-tool-dispatcher.py` | PostToolUse | advisory | Appends every tool name to `turn_tools_called`, which PreToolUse preconditions read. Edit/Write triggers a knowledge-capture checkpoint; Bash output is scanned for error patterns. |
| `mgcp-precompact.py` | PreCompact | advisory | Save context and write a soliloquy before context compression. |

[docs/mgcp-interception-flow.html](docs/mgcp-interception-flow.html) is the decision diagram
for all five.

### Routing is the LLM's job, not a regex

MGCP's first hooks matched user text with regex: `git-reminder.py` on "commit" and "push",
`catalogue-reminder.py` on library names, `task-start-reminder.py` on "fix" and "implement".
Three scripts, hundreds of patterns, and they missed nearly half of real messages — because
nobody says "let's commit this", they say "ship it", "push it up", or "ready to merge".

Classification is now the LLM's, against an intent map injected each turn. Benchmarked
against a ground-truth corpus (direct phrasing, indirect phrasing, false positives,
multi-intent, no intent, edge cases), self-routing beat regex by ~50% accuracy on half the
hook code. Graph-community classification — embed the message, search community summaries —
was measured too and lost badly: communities describe topics, not actions. It stays useful
for retrieval, just not for deciding what to do. The legacy regex hooks are archived in
`examples/claude-hooks/legacy/`.

### Routing is data

The eight intent definitions live in `~/.mgcp/intent_config.json`. Both hooks render their
prompt sections from that file, and REM's `intent_calibration` reads the same one. Adding an
intent is an `add_intent` call from chat and the next session picks it up — no code change,
no release. If the file is missing or corrupt the dispatcher falls back to a minimal built-in
set, so a fresh install never crashes a hook.

That closes a growth loop. REM runs community detection over the lesson graph and surfaces a
finding when a community has unmapped tags, or spans several intents with no dominant one
(< 60% share). Findings carry structured `proposed_patch` metadata, so: lesson community →
REM finding → `intent_config.json` update → next session's hook injection.

### Enforcement is data

The PreToolUse hook is a generic evaluator. Rules live in `~/.mgcp/enforcement_rules.json`
and take effect on the next tool call:

```jsonc
{
  "name": "git-requires-query-lessons",
  "enabled": true,
  "trigger": {"tool_name": "Bash",
              "command_match": {"type": "git_subcommand",
                                "subcommands": ["commit", "push"]}},
  "preconditions": [{"type": "tool_called_this_turn",
                     "tool_name": "mcp__mgcp__query_lessons"}],
  "bypass_scope": "git",
  "deny_reason": "git commit/push requires query_lessons first"
}
```

A **trigger** matches a tool name plus an optional Bash-command matcher (`git_subcommand`,
`regex`, `contains`). **Preconditions** are `tool_called_this_turn`,
`tool_not_called_this_turn`, `staged_files_coupling` (if you staged `src/**.py` you must also
stage `CHANGELOG.md`) and `tool_input_glob` (deny Edit/Write against `settings.json` or a
secrets path). A **bypass_scope** is a short token the user can name in `MGCP_BYPASS:<scope>`
to disable that one rule for a turn; bare `MGCP_BYPASS` disables all of them. Six MCP tools
CRUD rules from chat, so a new interrupt costs a sentence rather than a release.

Git detection is the part that had to be hardened repeatedly, and the shapes that beat it
were all ordinary: a newline instead of `&&`, an apostrophe in an unrelated sentence, a
global flag (`git -C /path commit`), an absolute path, `sudo`, a `VAR=value` prefix. Commands
are now tokenised per line with `shlex(punctuation_chars=True)`, global flags are skipped,
and a line that cannot be tokenised **fails closed for git only** — an unparseable command is
not evidence that it is safe, but blocking everything unparseable would stop unrelated work.

### The apology gate

"An apology must immediately trigger a knowledge write" was a passive note the LLM read at
session start and drifted away from by mid-session. It is now hard enforcement, and it is the
one gate that is not a data rule: its trigger is the assistant's own text rather than a tool
argument, so the hook carries it as a built-in.

Seven word-boundary regexes (`sorry`, `my bad`, `you're right`, `you are right`,
`my mistake`, `my apology/apologies`, `apologize/apologise`) run against the current turn's
assistant text. On a match, every tool call is denied except the two exits and the three
discovery tools — gating discovery would gate the exits themselves, and an escape hatch
behind the gate is a trap, not an escape hatch. A human can open it with
`MGCP_BYPASS:apology`, which is logged like everything else.

1. **Comply.** Write the lesson with `add_lesson`.
2. **Contest, on the record.** `adjudicate_apology_gate` records the flagged sentence, a
   verdict and ≥20 characters of reasoning. `not_apology` opens the gate for the rest of that
   turn in that session only; `apology` keeps it shut until the lesson is written — attesting
   "genuine" is never a route around capture.

Every denial, compliance, adjudication and human bypass appends to `~/.mgcp/gate_audit.jsonl`,
so claims about enforcement can be graded from evidence instead of asserted. REM summarises
it; SessionStart warns on a contest-rate spike.

This is the closest thing MGCP has to a self-improving loop, stated carefully: the system
detects a learning moment in its own output and refuses to proceed until the failure is
captured. Automatic trigger, enforced capture, LLM-authored content. It is a ratchet, not
learning — nothing gates the *quality* of the lesson. Known risks, accepted deliberately:
keyword detection is dodgeable by paraphrase, and a gate on apologies could train
apology-suppression rather than learning. A semantic detection tier was built and **measured
out** — BGE similarity classifies topic, not speech act, and lost to the regexes in both
directions on a pre-registered labelled set ([docs/scope-semantic-apology-gate.md](docs/scope-semantic-apology-gate.md)).
So were a widened acknowledgment tier, a quote stripper, a per-turn denial counter and an
advisory-degrade valve: five mechanisms built and deleted after red-teaming, six confirmed
defects between them. The build log is in
[docs/apology-gate-what-worked.md](docs/apology-gate-what-worked.md).

The control principle, transferable beyond MGCP: you don't need a perfect classifier if you
can force the agent to commit to an auditable attestation. Detection stays cheap and
imperfect, judgment is accountable, and override stays physically human-only.

### Intents compile to portable skills

Any intent, plus its linked workflow and that workflow's per-step lessons, compiles into a
single Anthropic-format `SKILL.md` at `~/.claude/skills/{intent}/SKILL.md` or
`<project>/.claude/skills/{intent}/SKILL.md`. A compiled skill is invocable as a slash command
and auto-discoverable by description, which gives an intent two firing channels on top of
MGCP's keyword gates — including in Claude surfaces where MGCP isn't installed.

Compilation is **purely additive**. The intent stays in `intent_config.json` and keeps driving
the hooks; the backing lessons stay in the active query pool; nothing is hidden, removed or
graduated. That is the inverse of an earlier strategy, since dropped, which hid graduated lessons from
`query_lessons` and measurably degraded reliability. The compiler was never the problem, so it
stayed: the intent is the source of truth and the SKILL.md is a downstream artifact you can
recompile any time.

## What 3.0 adds: more than one session at a time

Until 3.0, MGCP assumed one session. Embedded Qdrant allows a single writer per directory, so
a second session blocked on the lock — and so did the dashboard. Every process loaded its own
~448 MiB copy of the embedding model. Two sessions editing one lesson produced a lost update
that nothing anywhere recorded.

3.0 closes all three, in three independently droppable workstreams. Write-safety landed before
concurrency on purpose: shipping concurrency first would have turned a rare silent data loss
into a common one. **Embedded, single-session, no-daemon remains the default** — nothing below
is required, and MGCP still needs no server, no daemon and no container to run.

| | 2.x | 3.0 |
|---|---|---|
| Concurrent sessions | one writer; a second blocks on the Qdrant lock | opt in with `MGCP_QDRANT_URL` |
| Memory, 3 sessions | 1,344.6 MiB (each loads BGE) | **565.4 MiB** — one shared model |
| Cost per extra session | ~448 MiB, ~2,100 ms cold start | **39 MiB, ~65 ms** |
| Two sessions edit one lesson | last writer silently wins | `StaleWriteError` naming both versions |

### Two sessions, one store

```bash
docker run -p 6333:6333 -v ~/.mgcp/qdrant-server:/qdrant/storage qdrant/qdrant
export MGCP_QDRANT_URL=http://localhost:6333   # MGCP_QDRANT_API_KEY if the server wants one
mgcp-migrate --force                           # rebuild the index into the server from lessons.db
```

`--force` is what an existing install needs, because the local embedded directory is still
sitting there — and it removes that directory on the way through. SQLite is the source of truth,
so unsetting `MGCP_QDRANT_URL` and running `mgcp-migrate` again rebuilds the embedded index from
scratch.

Without server mode the embedded default no longer locks you out: SQLite and Qdrant open
separately, so project context, the soliloquy journal, lesson reads, workflows and six of REM's
seven operations work while another process holds the vector store. The ten tools that need
vectors say which PID holds it. Writes land in SQLite and are indexed when the store next opens
— `add_lesson` keeps working in particular, because it is the apology gate's only exit.

One resolver feeds all three Qdrant construction sites, so the two modes cannot disagree about
which store a process is talking to — a second session silently writing to the *other* store is
the failure that centralising this prevents. `/api/health` reports which mode is live, because
once two exist, that is not otherwise visible.

Verified against Qdrant 1.19.1 — two concurrent MGCP processes each wrote three lessons and each
read all six, with a third confirming the union. The verification found a showstopper first:
collection creation was check-then-act, which is safe embedded and a `409 Conflict` in server
mode, killing the second session — the entire audience for the feature. Another writer winning
that race is success, not failure, and both stores now tolerate it.

Honest cost: the Qdrant daemon is **474.9 MiB resident** for 656K of stored data, where the
design doc had estimated "tens of MB". Server mode buys correctness for roughly one extra
process's worth of memory. SQLite stays the source of truth and `mgcp-migrate` rebuilds the
index either way, so the move is reversible.

### Concurrent writes fail loudly

`update_lesson` takes an `expected_version` and the UPDATE carries `AND version = ?`; a zero
rowcount raises `StaleWriteError` rather than discarding the other session's edit.
`refine_lesson` tells the agent to re-read and re-apply, and the web editor returns 409.

The real clobber was never concurrency, though — it was granularity. `save_project_context`
replaced todos, notes, active files and decisions wholesale from whatever the caller had read,
so a session that only meant to change the notes discarded another session's todo. Narrow
single-column writers (`upsert_todo`, `append_decision`, `set_project_notes`,
`set_active_files`) remove that race rather than detecting it: SQLite serialises the writers,
so no token and no retry. Two sessions touching different fields of one project no longer
manufacture a conflict at all.

### One embedding model per machine

```bash
mgcp-embed            # run it explicitly, or let the first client start it
mgcp-embed --status   # which path is in use, and why
```

The daemon answers over a unix socket in `~/.mgcp/` — not TCP, so it is unreachable off-box and
needs no auth. Vectors are **bit-identical** to the in-process path, not merely close: the wire
carries raw little-endian float32, which is the dtype `encode` already produces, and the daemon
calls the same functions a local caller would. A test asserts exact equality so the two paths
cannot drift, which keeps the nine-month retrieval baseline comparable.

Measured round trip: **0.009 ms** persistent, 0.060 ms per connection, 0.014 ms for a `ping`
through the assembled daemon. End-to-end `embed` is 7.6-8.5 ms through the daemon against
7.8-9.5 ms in process — indistinguishable, and sometimes faster, because a client that never
imports torch is not holding its thread pool. The design doc had estimated 1-3 ms; measuring
corrected it by two orders of magnitude.

If no daemon answers, if it idles out mid-session, or if it returns an error, the caller falls
back in-process and never sees an exception. A dead daemon costs speed, never correctness.

```bash
MGCP_EMBED_DAEMON=0     # never use or start a daemon
MGCP_EMBED_AUTOSTART=0  # use one that is running, never start one
MGCP_EMBED_SOCKET=...   # socket path override
```

Every release before 3.0 — v1.0 through v2.13 — is in [CHANGELOG.md](CHANGELOG.md).

## Commands

| Command | Description |
|---------|-------------|
| `mgcp-init` | Configure LLM clients, deploy hooks, download embedding model |
| `mgcp` | Start MCP server |
| `mgcp-bootstrap` | Seed initial lessons and workflows |
| `mgcp-dashboard` | Start web UI |
| `mgcp-export` | Export lessons/projects to JSON |
| `mgcp-import` | Import lessons from JSON |
| `mgcp-duplicates` | Find semantically similar lessons |
| `mgcp-backup` | Backup/restore all MGCP data |
| `mgcp-migrate` | Rebuild the Qdrant index from `lessons.db` |
| `mgcp-embed` | Shared embedding daemon — load BGE once per machine instead of once per process (`--status`, `--stop`) |

## API & Dashboard

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/lessons` | All lessons |
| `GET /api/projects` | All projects |
| `GET /api/graph` | Graph visualization data |
| `GET /docs` | OpenAPI documentation |
| `WS /ws/events` | Real-time events |

## Beyond Software Development

The architecture is domain-agnostic. Replace the bootstrap with your own content:

| Domain | Example Lessons |
|--------|-----------------|
| **Customer Service** | Escalation triggers, resolution patterns |
| **Sales** | Objection handling, deal stage guidance |
| **Medical** | Symptom assessment, triage protocols |
| **Legal** | Document review, clause risk patterns |
| **Education** | Learning adaptation, concept explanations |

Same tools, different content.

## Agentic Workflows

> **Note:** We haven't built or tested this. Our focus is development workflows. The following is speculation about what the architecture *could* support.

Any agent operating across invocations faces statelessness. The components here - lessons, workflows, semantic search, hooks - could theoretically address that for agentic systems beyond coding assistants. We haven't tried it, but the pieces are there:

| Component | Potential Use |
|-----------|---------------|
| `add_lesson` / `refine_lesson` | Agent captures patterns from outcomes |
| `query_lessons` | Agent retrieves relevant guidance before acting |
| `workflows` | Multi-step processes with enforcement |
| Hooks (event triggers) | Inject context at decision points |

This wouldn't be machine learning - it would be **systematic accumulation** through explicit capture. A human adds lessons manually. An agent is not a person: for the agent, one capture path is already automatic and enforced — the apology gate blocks all tool use at acknowledged-failure moments until the lesson is written. What remains non-automatic is authorship and quality: the system forces *that* a lesson is captured, never *what* it says.

A hypothetical multi-agent pattern:

```
Agent A completes task -> explicitly adds lesson about edge case

Agent B starts related task -> queries lessons -> edge case surfaces
```

**What would be required to actually try this:**
- Hooks/triggers integrated with your agent framework's events
- Discipline around lesson capture (garbage in, garbage out)
- Tuning of triggers to match how your agents describe tasks

If someone tries this, we'd be interested to hear how it goes.

## Project Status

| Phase | Status |
|-------|--------|
| Basic Storage & Retrieval | Complete |
| Semantic Search | Complete |
| Graph Traversal | Complete |
| Refinement & Learning | Complete |
| Quality of Life | Complete |
| Proactive Intelligence | Complete |
| Feedback Loops (REM) | Complete |
| Skill Compilation | Complete — emits a SKILL.md file; never writes to the knowledge store. The *strategy* of graduating lessons out of `query_lessons` was dropped for degrading reliability. |
| Multi-session access | Complete (3.0) — Qdrant server mode, compare-and-swap writes, shared embedding daemon. Embedded single-session stays the default. |

## Contributing

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[O'Saasy License](https://osaasy.dev/) - Free for individual and internal use; commercial SaaS requires a license.

---

Built with the [Model Context Protocol](https://modelcontextprotocol.io/).
