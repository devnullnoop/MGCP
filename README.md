# Memory Graph Core Primitives (MGCP)

**Persistent context for stateless LLMs.**

[![License](https://img.shields.io/badge/License-O'Saasy-blue.svg)](https://osaasy.dev/)
[![Python](https://img.shields.io/badge/Python-3.11%20|%203.12-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

> **Alpha Software** - Actively dogfooding as we build. Working, but APIs may change.

## The Problem

LLMs are stateless. Every session starts from zero. The AI that helped you debug authentication yesterday has no memory of it today. Lessons learned, project context, architectural decisions - all gone the moment the session ends.

You've seen it: explaining the same codebase structure over and over, watching the AI repeat a mistake you corrected last week, losing important context when a session ends.

## What MGCP Does

MGCP gives your LLM **persistent context that survives session boundaries**.

```
Session 1: LLM encounters a bug -> adds lesson -> stored in database

Session 2: LLM has no memory of Session 1
         -> Hook fires: "query lessons before coding"
         -> Semantic search returns relevant lesson
         -> Bug avoided
```

**The primary audience is the LLM, not you.** You configure the system; the LLM reads from and writes to it. The knowledge persists even though the LLM doesn't.

### What makes this useful:

- **Semantic search** finds relevant lessons without exact keyword matches
- **Graph relationships** surface connected knowledge together
- **Workflows** sequence multi-step processes and surface the right lessons at each step — guidance, not a gate
- **Hooks** make it proactive - reminders fire automatically at key moments
- **Project isolation** keeps context separate per codebase

### What this is NOT:

- Not "AI that learns" - lessons are added explicitly
- Not self-improving in the strong sense - the system never authors or rewrites its own knowledge. One loop IS automatic: the apology gate detects acknowledged failure in the assistant's own words and refuses every tool call until a lesson is written (see v2.9). Capture is forced; content is still authored.
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

The dashboard was rebuilt in v2.13 as a single instrument panel. The eight pages it replaced each
answered "what is stored"; none covered enforcement, REM scheduling, the gate audit or the journal,
all of which shipped after the UI was last touched.

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
Per project, per operation, on that project's own session clock. Overdue and never-run states
carry a glyph and a word rather than a colour: `good` and `critical` measure a CVD ΔE of 4.1, so
hue alone cannot carry that distinction.
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
The second exit of the apology gate (v2.11): contest a tripwire fire on the record instead of being hard-blocked.
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

### The problem with regex routing

MGCP v1 used dedicated hook scripts to detect user intent from message text. `git-reminder.py` matched keywords like "commit" and "push". `catalogue-reminder.py` matched library names. `task-start-reminder.py` matched "fix", "implement", etc. Three scripts, hundreds of lines of regex patterns, and they missed nearly half of real user messages.

The failure mode was predictable: users don't say "let's commit this" — they say "ship it", "we're done here, push it up", or "ready to merge". Regex can't keep up with natural language variation. We also tested graph-community classification (embed the message, search community summaries) but it performed even worse — communities describe topics, not actions.

### LLM self-routing

v2 replaces all three regex hooks with a single routing prompt injected at session start (~800 tokens). The LLM classifies each message into 7 intent categories using its own language understanding, then follows an intent-action map to call the right tools.

We benchmarked all three approaches against a ground-truth corpus covering direct phrasing, indirect phrasing, false positives, multi-intent, no intent, and edge cases. LLM self-routing improved accuracy by ~50% over regex while cutting the hook codebase nearly in half. Graph-community classification was not competitive for intent detection (it remains valuable for knowledge retrieval, just not action classification).

### v2.2: routing prompt as data

LLM self-routing was a leap, but it was still hard-coded — three places had to be edited to add an intent (both hooks plus REM's `tag_to_intent` dict), and REM's intent_calibration findings were advisory only because there was no writeback path. v2.2 makes the routing prompt **data**: the canonical intent definitions live in `~/.mgcp/intent_config.json`, both hooks read pre-rendered prompt sections from that file, and REM intent_calibration loads from the same file. New `add_intent`/`update_intent`/`remove_intent` MCP tools let the LLM (or REM, or a human) modify the config from chat — the next session's hook injection picks up the change automatically. No code commit, no release.

A new `session_end` intent was added to fix a real failure mode that v2.1 silently ignored: messages like "bye bye now" had no intent classification, no keyword gate, and no calibration finding flagging the gap. v2.2 also adds a coherence check to REM intent_calibration — when a community spans multiple intents with no clear dominant (< 60% share), it surfaces a finding suggesting a new intent or tag remap. This catches misfit clusters that defensive over-mapping in v2.1 silenced.

### v2.3: compile intents to portable skills

Anthropic's plugin system distributes prompt-only "skills" as `SKILL.md` files in `~/.claude/skills/`. Once installed, a skill is invocable as a slash command (`/skill_name`) and auto-discoverable by Claude via its frontmatter description. v2.3 adds a compiler that takes any MGCP intent + its linked workflow + the workflow's per-step lessons and renders all four layers into a single SKILL.md — turning MGCP's accumulated discipline into a portable artifact you can use even in Claude surfaces that don't have MGCP installed.

The compiled skill is **purely additive**. The intent stays in `intent_config.json` and continues to drive the hook keyword gates and LLM intent classification. Backing lessons stay in the active query pool. Compiling does not remove, hide, or graduate anything. This is the inverse of the Phase 8 *strategy*, which was dropped for degrading reliability — Phase 8 graduated lessons out of `query_lessons`, which hid knowledge from the LLM. The compiler was never the problem and was not removed. v2.3 keeps the source of truth in MGCP and treats the SKILL.md as a downstream export format that can be recompiled at any time.

A new `compile_intent_to_skill` MCP tool, a `POST /api/intent-config/intents/{name}/compile` web endpoint, and a "Compile skill" button in the instrument panel's Curate view all converge on the same `compile_intent_to_skill()` function.

### v2.3 hook templates: enforcement, not just advice

All prior hooks (SessionStart, UserPromptSubmit, PostToolUse, PreCompact) are **advisory** — they inject text as `<system-reminder>` tags that the LLM can skim or ignore. The `query-before-git-operations` lesson failed v1 → v4 across months despite the hook firing correctly every time; interception was not compliance.

v2.3 adds a `PreToolUse` hook (`pre-tool-dispatcher.py`) that can actually refuse a tool call by returning `permissionDecision: "deny"`. First enforced rule: `git commit` / `git push` is blocked unless `mcp__mgcp__query_lessons` ran in the same turn. The detector uses quote-aware tokenization (`shlex` with `punctuation_chars=True`), so `grep 'git commit' docs/` and `echo "how to git commit"` correctly pass through while `make build && git push` correctly blocks. See `docs/mgcp-interception-flow.html` for the full interception map, the growth loop, and candidate improvement areas.

### v2.4: enforcement-as-data

v2.3 introduced enforcement but the rule was **hardcoded** in the hook. Adding a new interrupt — "before `rm -rf`, require a confirmation tool", "if you staged `src/**.py`, you must also stage `CHANGELOG.md`" — meant editing Python and shipping a release. That's the same drift pattern v2.2 fixed for intent routing.

v2.4 makes the routing prompt's philosophy universal: **enforcement is now data**. Rules live in `~/.mgcp/enforcement_rules.json`. The `pre-tool-dispatcher.py` hook is a generic stdlib-only evaluator — it reads the JSON on every tool call and applies every enabled, triggered, non-bypassed rule. Adding a new rule is a chat-time `add_enforcement_rule` call; the next tool call picks it up with no restart.

Each rule has three parts: a **trigger** (which tool calls it matches — tool name plus optional Bash-command matcher: `git_subcommand`, `regex`, or `contains`), one or more **preconditions** (`tool_called_this_turn`, `tool_not_called_this_turn`, `staged_files_coupling`), and a **bypass_scope** (short token like `"git"` or `"docs"` the user can name in `MGCP_BYPASS:<scope>` to disable that rule for one turn; bare `MGCP_BYPASS` disables all).

Six new MCP tools (`list_enforcement_rules`, `get_enforcement_rule`, `add_enforcement_rule`, `update_enforcement_rule`, `remove_enforcement_rule`, `toggle_enforcement_rule`) let the LLM — or a human — CRUD rules from chat. The `git-requires-query-lessons` rule from v2.3 is preserved as a seeded default. Tool count: 43 → 49.

### v2.5: SessionStart dedup

The `session-init.py` hook previously injected a full `<intent-routing>` + `<intent-actions>` pair on top of the per-turn copy already rendered by `user-prompt-dispatcher.py`. Two copies of the same 8-intent choreography in the same conversation — competing for attention and burning ~300 tokens per session — with no behavioral payoff, since the dispatcher block survives context compaction and the SessionStart block did not carry any information the dispatcher didn't.

v2.5 drops the SessionStart copy. The hook now carries only the bootstrap checklist (`read_soliloquy` / `get_project_context` / `query_lessons`) and the workflow execution discipline. SessionStart injection drops ~2500 → ~1050 chars. The dispatcher is unchanged. This walks back an earlier sketch of moving intent classification into the hook (keyword regex) — that direction would have regressed to the legacy hooks archived in `examples/claude-hooks/legacy/`; classification stays LLM-side and enforcement rules catch misclassification at tool-call time regardless.

### v2.6: Stale hook reference self-detection

Two coupled fixes for an upgrade-path bug. On installs that ran `mgcp-init` between v2.0/v2.1 (when `mgcp-reminder.py` was a real hook) and v2.2+ (where it was superseded by `post-tool-dispatcher.py`), the installer would delete the file from `~/.mgcp/hooks/` but leave the reference in `~/.claude/settings.json` — because the settings-scrub was gated behind `--force`. Every matching `PostToolUse` tool call then surfaced `hook returned blocking error` / `Errno 2: No such file` noise. The Write itself always succeeded — PostToolUse has no blocking authority — but the UI wording was misleading.

1. **Fix the installer.** The legacy-command scrub now runs on every `mgcp-init`, not just `--force`. Removing a legacy hook file and removing the `settings.json` reference to it are two halves of the same cleanup; separating them was the bug.
2. **Detect at session start.** `session-init.py` now scans `settings.json` for hook commands pointing at missing absolute `.py` paths and injects a `## ⚠️ Stale Hook References Detected` block telling me (and the user) to run `mgcp-init --force`. One `stat()` per hook command per session; fails open on parse errors.

If you're upgrading from v2.0 or v2.1 and were ever seeing "hook returned blocking error" noise, run `mgcp-init --force` once to clean up.

### v2.7: REM enforcement gate + visibility

REM cycles have no auto-trigger. The schedule lives in `rem_state` and operations move forward only when something calls `mcp__mgcp__rem_run`. On this project the schedule fell 13 sessions overdue (about 2 months) before anyone noticed, because the only way to surface that was to read `rem_status` manually. The advisory channel had failed silently. v2.7 closes both halves of that gap:

1. **Seeded enforcement rule (default-off).** `rem-required-before-commit` is now in `DEFAULT_RULES` in `src/mgcp/enforcement.py`. When enabled, it blocks `git commit` / `git push` unless `rem_run` was called in the same turn. Default-off because fresh installs without lesson history do not benefit from REM enforcement; toggle it on via `mcp__mgcp__toggle_enforcement_rule('rem-required-before-commit')` once REM is producing useful findings. Bypass scope `rem`.
2. **SessionStart visibility layer (always on).** `session-init.py` now reads `~/.mgcp/lessons.db` directly (stdlib `sqlite3`, read-only URI mode, 2-second timeout) and injects a `## ⚠️ REM Operations Overdue` block listing every operation whose `next_due_session` is at or below the project's `session_count`. Each entry shows last-run session, due-at session, and the gap in sessions. The action footer recommends `rem_run` and points at the optional toggle for commit-time enforcement.

Together: the rule is opt-in commit-time enforcement, the detector is always-on session-start visibility. The rule fires at the moment of shipping discipline; the detector fires at the moment of attention.

v2.7 also seeds `version-bump-requires-readme` in `DEFAULT_RULES`. That rule was added out-of-band via `add_enforcement_rule` after v2.4 shipped and the recent-decisions log called for seeding it next session. v2.7 is that next session.

**Existing installs do not auto-migrate the new defaults.** The seed in `init_project.py:720` writes `~/.mgcp/enforcement_rules.json` only when the file does not already exist (preserves user edits). Pick up the new rules on an existing install by calling `mcp__mgcp__add_enforcement_rule` for each, or delete the rules file (loses local edits) and re-run `mgcp-init`. The SessionStart REM-overdue detector activates immediately on upgrade because it reads the DB directly, not via the rules file.

### v2.8: the tool_input_glob precondition

A small enforcement-as-data extension: rules can now gate on a tool's own input via `tool_input_glob` (`field` + `deny_globs`), e.g. denying Edit/Write against `settings.json` or secrets paths. Fails open on a missing field. Hook payload version 2.8; no new hook files.

### v2.9: the apology gate

The MEMORY.md rule "an apology must immediately trigger a knowledge write" had been a passive note the LLM read at session start and drifted away from mid-session — the same failure mode as every advisory mechanism above. v2.9 promotes it to hard enforcement, and it is the one gate that is **not** a data rule in `enforcement_rules.json`: its trigger is the assistant's own text, not a tool argument, so the PreToolUse hook carries it as a built-in.

Mechanics: seven word-boundary regexes (`sorry`, `my bad`, `you're right`, `you are right`, `my mistake`, `my apology/apologies`, `apologize/apologise`) run against the current turn's assistant text. On a match, **every tool call is denied except `add_lesson`** until a lesson is written; the gate clears on the next user prompt. Bypass: `MGCP_BYPASS:apology`. (v2.12 fixed an escape: tool results are recorded as user-typed transcript entries, so any tool call — even a denied one — used to truncate the gate's view of the turn and reopen it; the transcript walk now stops only at genuine user prompts.)

This is the closest thing MGCP has to a self-improving loop, stated carefully: the system detects a learning moment in its own output and refuses to proceed until the failure is captured — automatic trigger, enforced capture, LLM-authored content. It is a ratchet, not learning: nothing gates the *quality* of the lesson written. Known risks, accepted deliberately: keyword detection is dodgeable by paraphrase (an agent was once caught drafting lessons that coached phrasing around the classifier — they were deleted), and a gate on apologies could train apology-suppression instead of learning. Both are measurable over time; neither is a reason to go back to advice. A semantic detection tier was attempted and **measured out** on 2026-07-29 — BGE similarity classifies topic, not speech act, and lost to the regexes in both directions on a pre-registered labelled set. The numbers and the reusable acceptance set live in [docs/scope-semantic-apology-gate.md](docs/scope-semantic-apology-gate.md).

### v2.10: the detector holds for ordinary command shapes

The v2.3 gate's detector needed hardening. Quote-aware tokenization alone was not enough, and three shapes walked straight past the gate until 2026-07-29, when replaying a commit this repo's own gate had just allowed exposed all three. A newline is a command separator, but `shlex` with `whitespace_split` consumes it, so `cd /repo` ⏎ `git commit` tokenized as one command and `git` no longer sat at a command boundary — `&&` was handled, the newline every multi-line block uses was not. An unterminated quote made the detector report "not a git command", so any message containing an apostrophe (`the project's fix`) turned the git gates off. And global flags pushed the subcommand one slot along, so `git -C /path commit` read its subcommand as `-C` and matched nothing.

Detection now runs per line, skips git's global flags, and **fails closed** when a line cannot be tokenized: a command the detector cannot parse is not evidence that the command is safe. Failing closed stays scoped to git — `echo don't` is still allowed — because blocking everything unparseable would stop unrelated work. The fix had to be written twice, once in `enforcement.py` and once in the stdlib-only hook, and that is precisely why one bug lived in two implementations while both suites stayed green. v2.13 removed the second copy: the hook is now the only detector, because it was always the only one that ran (see below). **Upgrading the package does not redeploy hooks** — run `mgcp-init --force` to pick this up, or an existing install keeps the vulnerable detector.

### v2.13: the copies that never ran, and three channels that were never connected

A whole-module review (10 readers over disjoint slices, every finding then put to two
refute-by-default verifiers) found that several mechanisms this README describes were
wired to nothing. Each item below is a *measured* failure, not a tidy-up.

**The apology gate's contest exit had never once opened the gate.** The hook requires the
adjudication's `session_id` to match the harness session exactly; `adjudicate_apology_gate`
took that id as an argument, and nothing anywhere tells the model what it is — while the
gate is armed the model cannot even read the transcript to look it up. The live audit log
settles it: 93 denials carrying harness UUIDs, 16 adjudications carrying `""` or an
invented API-style id, intersection empty. The id now comes from `turn_session_id`, which
UserPromptSubmit records each turn, and the parameter is gone — a value the caller cannot
know is not a parameter. Point 2 above finally describes what the code does.

**`schedule_reminder` and `update_workflow_state` wrote to a file no hook reads.**
`reminder_state.py` wrote `~/.mgcp/reminder_state.json`; every hook reads
`~/.mgcp/workflow_state.json`. Both files were live on disk with divergent counters
(`current_call_count` 0 vs 3). The self-directed reminder channel had been inert since
2026-02-10. One file now, not two halves of a channel that never met — and `reset_state`
merges instead of replacing, since that file also holds the per-turn enforcement keys.

**REM's duplicate scan could not run in-process, and said the corpus was healthy anyway.**
`find_duplicates` constructed its own `QdrantVectorStore`, but local-mode Qdrant permits
one client per path, so inside the MCP server it always raised — and the `except` returned
`[]`, which renders as "Knowledge base looks healthy". A scan that cannot run now reports
that it could not run. The engine passes its live client down.

**`context_history` grew a row per write, not per session.** 1,315 rows for 212 real
sessions. A REM operation gated on `len(history) >= 10`, treating the rows as
"snapshots spanning sessions", so it fired after one or two. Now upserted on
`(project_id, session_number)`; an idempotent migration collapses existing rows to the
newest per session. On this repo's own store that took the database from 14.4 MB to
3.5 MB after `VACUUM`.

**`spider_lessons` returned different lesson sets on different runs.** `get_related`
returned a `set`, whose iteration order over strings varies between processes, and
`spider` turned neighbour order into which nodes fell outside the depth limit. Worse, a
node first reached *too deep* was marked visited, so a later shallower path to it returned
early and its in-limit children were never traversed. Neighbours are sorted and traversal
tracks best-depth per node.

**The enforcement evaluator existed twice and ran once.** `server.py` imports the schema
and `load_config`/`save_config`; every evaluator symbol in `enforcement.py` —
`evaluate_rules`, `trigger_matches`, `evaluate_precondition`, `check_coupling`,
`detect_git_subcommand`, `parse_bypass_scopes` — was imported only by its own tests. The
"shared behavioral contract" was two suites over two implementations, one of which decided
nothing. The copy is deleted (573 → 260 lines) and its unit tests were retargeted at the
hook, the code that actually runs. That retargeting exposed a real gap: the live
`MGCP_BYPASS` parse in UserPromptSubmit had **no** test, because the only tests of that
logic were testing the dead copy.

**A corrupt rules file was silently replaced.** `load_config` returned `DEFAULT_RULES` for
a file that existed but did not parse, and every write tool then persisted the
substitution — overwriting rules the hook was still enforcing out of that same file. A
missing file still yields defaults (a fresh install has no other truth); an unparseable
one now raises, and the calling tool reports it.

Also: error detection was blind to stderr (it preferred `stdout` and returned early, so a
command that failed with an empty stdout read as clean); `mgcp-migrate --dry-run` exited
non-zero without previewing on any install that already had Qdrant data; and
`update_workflow_state`'s "new workflow resets step tracking" branch was unreachable
because the state was assigned before the comparison.

**The suite could not be run to completion, and that is how all of this stayed hidden.**
v2.12 recorded the test-suite exit hang as fixed. It was not. `pytest
tests/test_failure_recovery.py` printed `28 passed in 3.1s` and then never exited; a
`faulthandler` dump put the main thread in `threading._shutdown` with two live aiosqlite
worker threads. Those threads are non-daemon, so one orphan blocks shutdown forever. The
v2.12 hook stopped connections sitting in a store's pool, but the leak starts elsewhere:
when schema init raises on a corrupt database, the connection just opened is neither
pooled nor closed — and `test_failure_recovery.py` is precisely the file that corrupts
databases on purpose. Initialisation failure now closes its connection, and the exit hook
tracks connections rather than only pools. The suite now runs end to end: 866 passed,
1 xfailed, process exits in 60s.

**Deleted, each after a repo-wide usage sweep:** `examples/claude-hooks/*.py` (1,314 lines
byte-identical to `src/mgcp/hook_templates/`, no reader, no sync mechanism, already drifted
once); `llm-memory-mcp-design.md` (516 lines, pre-v2.2, contradicting the shipped system);
`check_install.py` (288 lines, no caller, a numpy pin contradicting pyproject);
`data_ops.suggest_tags` (no caller but its own four tests); `tests/test_smoke.py`
(subsumed — its one unique assertion, the client count, moved into
`test_init_project.py` and strengthened from 5 spot-checks to all 8 exactly);
`TestMemoryUsage` in `test_stress.py` (misused `ru_maxrss` twice: it is peak RSS, so the
delta can only be ≥0, and on macOS it is already bytes so `* 1024` inflated it 1024× —
the file is `slow`-marked and CI runs `-m "not slow"`, so it failed locally and never ran
remotely); `TestGapDetection` in `test_trigger_coverage.py` (both tests print a report and
assert nothing); and two tables in `lessons.db` that duplicated `telemetry.db`'s schema
and held zero rows after 102 sessions.

**Enforcement was bypassable four ways, and none of them were exotic.** A second pass applied the
remaining 89 findings. Four of them were ways a gated tool call got through: `git` detection missed
an absolute path (`/usr/bin/git commit`), a wrapper (`sudo git commit`) and a `VAR=value git commit`
prefix; a *string* rather than a list in `turn_tools_called` made `in` match substrings, so one
malformed state write opened every gate; an empty `"command_match": {}` matched every call to the
tool instead of none; and `MGCP_BYPASS:GIT` did not bypass, because the scope was compared
case-sensitively against a lowercase `bypass_scope`. Separately `get_ancestors` **span forever on a
parent cycle** — which `mgcp-import` can create — and `get_statistics` walks every node's ancestry,
so one cycle hung the call. Row parsers swallowed malformed JSON into empty edges rather than
reporting corruption. REM reported a clean run for a misspelled operation name *and* wrote a junk
schedule row that the SessionStart detector then reported as overdue. And one test was deleted for
triggering an unbounded 415 MB model download to assert something that could not fail.

**Two documents were rebuilt rather than patched.** `docs/mgcp-interception-flow.html` described
v2.4 — 553 lines and 8 diagrams with no mention of `apology`, `adjudicate`, `gate_audit`,
`turn_session_id` or `rem_state`, on a document whose whole subject is enforcement, while CLAUDE.md
sent readers to it for "the full interception map". CLAUDE.md's hook table had become
changelog-in-a-table (`v2.5: … v2.6: … v2.7: … v2.11: …`), which is exactly why it kept going
stale: each release appended a clause instead of restating the present. Both now describe current
behaviour, and `docs/CAPABILITIES.md` lost 30 line-number anchors that had rotted three times in
one day. Ledger row **E05** — which had asked for "a measurement of whether bridging surfaces
useful neighbours" and correctly predicted the `0.0` score — moved to VERIFIED.

**One finding was rejected on review.** A reader proposed cutting ~627 of the 682 lines of
`tests/test_intent_benchmark.py` as "asserting nothing or testing its own constants". Both
verifiers upheld it; both were wrong. `classify_regex` and `GraphCommunityClassifier` are
defined in that file because they are the two *baselines* behind the measured claim above —
"LLM self-routing improved accuracy by ~50% over regex … graph-community was not
competitive". Deleting them would have orphaned a published result. The file stays.

### v2.11: a second exit, and a record

The v2.9 gate had one exit: write the lesson. Two problems surfaced when it was tested properly.

**It could deadlock.** Both the comply exit and any contest exit are *tool calls*, and in a harness that loads tool schemas on demand, reaching them requires a discovery call — which the gate was also denying. An agent could be locked in a room with the key inside; only a human `MGCP_BYPASS` freed it. This was latent in v2.9 and became likely the moment the tripwire widened.

**A denial left no trace.** Nothing recorded that the gate had fired, so no claim about enforcement could ever be graded from evidence.

v2.11 adds exactly three things, and each one survived adversarial review:

1. **Discovery calls are never gated** (`ToolSearch`, `ListMcpResourcesTool`, `ReadMcpResourceTool`). Stateless, exact-match, and it cannot mutate anything. This is the whole deadlock fix.
2. **A second exit**: `adjudicate_apology_gate` records a verdict, the flagged text, and ≥20 characters of reasoning to the audit log. `not_apology` opens the gate for the rest of *that turn, in that session only* — the hook matches the session id exactly and UserPromptSubmit clears the verdict on the next prompt; `apology` keeps it shut until the lesson is written — attesting "genuine" is never a way around capture.
3. **An append-only audit log** at `~/.mgcp/gate_audit.jsonl`: every denial (gate *and* data rules), every compliance with its lesson id, every adjudication with its reasoning, every human bypass. REM's `gate_audit_review` summarises it and surfaces contested verdicts; SessionStart warns on a contest-rate spike.

**What was built and deliberately cut.** A widened acknowledgment tier, a quote-and-code stripper, a first-person sentence window, a per-turn denial counter, and an advisory-degrade valve were all implemented and then removed after red-teaming. They produced six confirmed defects between them — the valve short-circuited *every other enforcement rule*, a malformed counter crashed the hook into a silent unaudited bypass, and the quote-stripper's apostrophe handling stopped `you're right` from firing at all. The reduction is the result, not a compromise: each mechanism added to handle a failure mode generated two more.

A build log of this whole effort — the two detectors that were measured and rejected, the five mechanisms that were built and deleted, and the deadlock that cost three verification runs — is in [docs/apology-gate-what-worked.md](docs/apology-gate-what-worked.md).

The control principle, transferable beyond MGCP: you don't need a perfect classifier if you can force the agent to commit to an auditable attestation. Detection stays cheap and imperfect; judgment is accountable; override remains physically human-only. And an enforcement layer needs its exits to be *reachable* — a gate whose escape hatch is behind the gate is a trap.

### Current hooks

| Hook | Event | Type | Purpose |
|------|-------|------|---------|
| `session-init.py` | SessionStart | advisory | Inject the session-start bootstrap checklist (read_soliloquy / get_project_context / query_lessons) and workflow execution discipline. v2.5: no longer duplicates the dispatcher's routing/actions block. v2.6: detects stale `.py` hook references in settings.json. v2.7: detects overdue REM operations from `rem_state` and recommends `rem_run`. |
| `user-prompt-dispatcher.py` | UserPromptSubmit | advisory | Hard keyword gates (data-driven from `intent_config.json` — both git and session_end fire from one loop), terse routing re-injection every message, `turn_session_id` and `MGCP_BYPASS[:scope]` capture, scheduled reminders, workflow state, per-turn enforcement state reset |
| `pre-tool-dispatcher.py` | PreToolUse | **enforcing** | Generic evaluator reading `~/.mgcp/enforcement_rules.json`. Applies every enabled rule; denies when preconditions unsatisfied. Scoped bypass via `MGCP_BYPASS:<scope>` or bare `MGCP_BYPASS` |
| `post-tool-dispatcher.py` | PostToolUse | advisory | Routes by tool: Edit/Write triggers knowledge-capture; Bash triggers error detection; appends every tool name to `turn_tools_called` for PreToolUse rules |
| `mgcp-precompact.py` | PreCompact | advisory | Save context (and write_soliloquy) before compression |

The dispatcher falls back to a minimal hard-coded intent set if the JSON file is missing or corrupt — a fresh install never crashes a hook. Legacy regex hooks (`git-reminder.py`, `catalogue-reminder.py`, `task-start-reminder.py`) are archived in `examples/claude-hooks/legacy/`.

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

This wouldn't be machine learning - it would be **systematic accumulation** through explicit capture. A human adds lessons manually. An agent is not a person: for the agent, one capture path is already automatic and enforced — the v2.9 apology gate blocks all tool use at acknowledged-failure moments until the lesson is written. What remains non-automatic is authorship and quality: the system forces *that* a lesson is captured, never *what* it says.

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
| Skill Compilation | Complete (v2.3) — emits a SKILL.md file; never writes to the knowledge store. The *strategy* of graduating lessons out of `query_lessons` was dropped for degrading reliability. |

## Contributing

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[O'Saasy License](https://osaasy.dev/) - Free for individual and internal use; commercial SaaS requires a license.

---

Built with the [Model Context Protocol](https://modelcontextprotocol.io/).
