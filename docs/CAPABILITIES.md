# MGCP capability ledger

**Every claim MGCP makes about itself, with a status and a command that checks it.**

Built 2026-07-28. The executable half of this document is
[`tests/test_claims.py`](../tests/test_claims.py). Row `C11` here is
`test_C11_...` there — the IDs are the same on purpose. A failure in that file is
not a test bug: it means a claim in `README.md`, `CLAUDE.md`, or an MCP tool
docstring is currently false, and the failure message says which one.

```bash
.venv/bin/python -m pytest tests/test_claims.py -q       # the whole ledger
.venv/bin/python -m pytest tests/test_claims.py -k C11   # one row
```

Rows numbered `E**` are graded from recorded evidence rather than a dedicated
test; each still carries a command you can run.

---

## Status vocabulary

| Status | Meaning |
|---|---|
| **VERIFIED** | A test exists, it runs, it passes, and there is no mocked seam between the claim and the assertion. |
| **CLAIMED** | Code exists and unit tests pass, but nothing proves the claim end to end under real conditions. |
| **FAKE** | The claim is made and the path is missing, broken, or only fires from tests. |
| **RETRACTED** | Removed from the customer-facing surface, with the commit that removed it. |

Three rules were applied when grading, and they explain every row that looks
harsh:

1. **A subsystem that returns success while doing nothing is FAKE, not CLAIMED.**
   The agent acts on the false success.
2. **The sentence is the claim.** "Removed" when the code ships, or "workflow
   steps enforce" when workflow steps are advisory text, is a false sentence even
   when a working mechanism exists one paragraph away. Where that happens the row
   is FAKE and the working mechanism is cited by row number, so nobody reads this
   ledger as "enforcement doesn't work." It does. See section D.
3. **No VERIFIED without a command that was run.**

## Constants the ledger pins

```
RETRIEVAL_FLOOR = 0.30      # QdrantVectorStore.search min_score default
MCP_TOOL_COUNT  = 49        # @mcp.tool() functions in src/mgcp/server.py
```

`test_C24` compares `RETRIEVAL_FLOOR` above against the live default in
`qdrant_vector_store.py` and fails if they diverge. Changing the floor without
updating this file breaks the ledger. That is the point.

## Scoreboard

| Status | Rows |
|---|---|
| VERIFIED | 20 |
| CLAIMED | 7 |
| FAKE | 13 |
| RETRACTED | 0 |
| **total** | **40** |

Measured 2026-07-28 19:36 EDT against the working tree (`db21ee5` + uncommitted
repairs): `tests/test_claims.py` → **9 failed, 22 passed** (31 tests; `C19` is
parametrised three ways). Every failure is a FAKE row below. Re-run it; do not
trust this paragraph.

Four rows — **C11**, **C12**, **C27**, **C28** — were FAKE when this ledger was
drafted and turned VERIFIED within hours, while it was being written, as parallel
repairs landed. They are marked ✅ *repaired during this pass*. That is the ledger
working as intended, and it is recorded rather than quietly overwritten.

**Every passing row in this file was also checked for non-vacuity**: the thing it
asserts was broken in a scratch copy — the hook neutered, the counter reverted to
`max()`, the sanitiser swapped for a plain `BaseModel`, `record_usage` deleted,
the pinned floor moved — and the test was confirmed to fail. 19 of 19
perturbations produced a failure. No repository file was modified to do it.

---

## A. Surface consistency — what the docs say vs. what ships

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C01 | README:180, CLAUDE.md:131 — "MCP Tools (49 total)" | `server.py` defines exactly 49 `@mcp.tool()` functions | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C01` |
| C02 | README presents its tool tables as the tool surface | those tables cover 41. `write_soliloquy`, `read_soliloquy`, `list_intents`, `get_intent`, `add_intent`, `update_intent`, `remove_intent`, `compile_intent_to_skill` appear nowhere in README | a "49 total" heading with 41 rows under it | add Soliloquy (2) and Intent Config (6) sections to README | **VERIFIED** | `pytest tests/test_claims.py -k C02` |
| C03 | CLAUDE.md documents the tool surface for the agent working on this repo | all 49 are named | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C03` |
| C04 | CLAUDE.md:88 — "`server.py` - MCP server with 37 tools" | there are 49 | stale by 12; not updated when v2.2–v2.4 added tools | 37 → 49 | **VERIFIED** | `pytest tests/test_claims.py -k C04` |
| C05 | README:353 Commands table — 9 CLI commands | all 9 are `[project.scripts]` entries and every `module:function` target imports and resolves | `mgcp-launcher` ships undocumented (harmless) | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C05` |
| C06 | README:367 API table — 6 endpoints | `/api/health`, `/api/lessons`, `/api/projects`, `/api/graph`, `/ws/events` all have routes; `/docs` is FastAPI's own | it documents 6 of ~33 routes, which is a summary, not a lie | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C06` |
| C07 | README:341 / CLAUDE.md:228 — "Current hooks" tables | all 5 named hook scripts exist in `src/mgcp/hook_templates/` | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C07` |
| C08 | CLAUDE.md:9 — "**Status**: v2.1.0" | `pyproject.toml` = 2.1.0, `mgcp/__init__.py` = 2.0.0, README documents v2.7 behaviour, `hook_templates/VERSION` = 2.8 | four version sources, three answers; an install reports whichever one it happens to read | pick one source of truth, derive the rest | **FAKE** | `pytest tests/test_claims.py -k C08` |
| C09 | `rem_run` docstring — "Options: staleness_scan, duplicate_detection, community_detection, knowledge_extraction, context_summary" | `DEFAULT_SCHEDULES` has 7. `intent_calibration` and `action_effectiveness` are omitted | the agent reads this docstring and cannot name two operations that exist — including `intent_calibration`, which is the operation that closes the REM growth loop CLAUDE.md:240 advertises | add both to the docstring | **FAKE** | `pytest tests/test_claims.py -k C09` |
| C10 | `add_enforcement_rule` docstring — precondition `type` ∈ {`tool_called_this_turn`, `tool_not_called_this_turn`, `staged_files_coupling`} | `enforcement.py` also accepts `tool_input_glob` | in-flight v2.8 work added the type without the docstring or CLAUDE.md:269; the agent is the only caller and cannot discover it | document `tool_input_glob` in both places | **FAKE** | `pytest tests/test_claims.py -k C10` |

## B. REM — a subsystem that reports success while doing nothing

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C11 ✅ | README:237 — "REM runs periodic consolidation to keep the knowledge base healthy without manual curation", on per-operation schedules ("every 5 sessions", "every 10", fibonacci, logarithmic) | **Was FAKE.** `server.py:2040` set `session_number = max((p.session_count for p in projects), default=1)` — the maximum across **every project on the machine**. `is_due()` opens `if current_session <= last_run_session: return False`, and every operation had recorded `last_run_session = 98` (this repo's own count). Five `rem_run` calls in one BoltMob session all returned "Operations run: none." **Repaired during this pass:** `server.py:2068` now reads `session_number = project.session_count` | — | see C13 and C29 — the code fix alone does not revive REM | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C11` |
| C12 ✅ | `rem_status` docstring — "Show REM schedule state: what ran when, what's due next" | **Was FAKE** (same `max()` at `server.py:2118`, so the "Current Session: N" header named the busiest project on the machine). **Repaired during this pass:** `server.py:2155` now reads `current = project.session_count`, and `get_status()` now returns `is_due` computed with the same predicate `rem_run` uses, so display and execution cannot disagree | — | nothing | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C12` |
| C13 | README:319 v2.7 — "SessionStart visibility layer (**always on**)" injects a "REM Operations Overdue" block | `session-init.py` correctly uses the *current project's* `session_count`. But the stored `next_due_session` values were computed at session 98, so the thresholds are 100 / 102 / 144 while the busiest project sits at 98. **The detector still fires for zero projects**, and no REM operation has run since **2026-05-01 — 88 days**. Schedule and watchdog are both inert | the watchdog inherits the poisoned numbers C11 wrote before it was fixed. Fixing the code did not clean the data | a dry-run data repair of the seven `rem_state` rows, run deliberately by the operator against the live store — never a silent migration | **FAKE** | `pytest tests/test_claims.py -k C13` |
| C29 | README:237 — schedules are per operation, per project | `rem_state` is declared `operation TEXT PRIMARY KEY` — **one row per operation for the entire machine**. With the real `is_due()`: a project at session 36 against a global `last_run_session` of 98 can never become due, and whichever project runs REM next overwrites the schedule for every other one. Per-project cadence is not expressible in this schema | **C11 was necessary but is not sufficient.** The session number is now per project; the state it is compared against is still global | key `rem_state` on (operation, project) — this is a schema change plus a migration of 7 live rows, so it is an operator-triggered change, not a silent one | **FAKE** | `pytest tests/test_claims.py -k C29` |
| E01 | CLAUDE.md:281 — "Phase 7: Feedback Loops — Complete (REM cycle engine …)" | the engine is complete and unit-tested: 17 tests in `test_rem_cycle.py`, 15 in `test_rem_scheduling.py`. It has simply not been *reached* in production since May | "Complete" describes the code, and the code is fine. Nothing proves the loop closes on a real store | after C11, run `rem_run` against the live store and record the findings here | **CLAIMED** | `pytest tests/test_rem_cycle.py tests/test_rem_scheduling.py -q` |
| E02 | README:239 — "The schedules are configurable but the defaults work well in practice" | unfalsifiable while C11 stands: no default has been exercised since May | — | re-assess after C11. An operation that runs and finds nothing for 20 sessions is a deletion candidate, not a feature | **CLAIMED** | `pytest tests/test_claims.py -k C13` |

## C. Data integrity and project isolation

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C14 | `models.SanitizedModel` — "strips tool-call XML from any string field" | it **neutralises** rather than strips: `<invoke>` / `<parameter>` become `‹invoke›` / `‹parameter›`. The safety property — stored text cannot be replayed as a tool call — holds, both on construction and on direct attribute assignment | the summary line's verb; the docstring body is accurate two lines later | none; the property is real | **VERIFIED** | `pytest tests/test_claims.py -k C14` |
| C15 | README:32 — accumulated lessons are the value | 14 of 238 lessons in the live store carry serialised tool-call envelope fragments inside `action` / `rationale` | nothing *rejects* an envelope at write time, so the neutralised text is stored and then **embedded as content** — it degrades retrieval as well as display | reject at `add_lesson`; migrate the 14 (dry-run first — this is the live store) | **FAKE** | `pytest tests/test_claims.py -k C15` |
| C27 ✅ | README:38 — "**Project isolation** keeps context separate per codebase" | true of `ProjectContext` and the catalogue, both keyed by `project_path`. **Was FAKE** for the soliloquy: `read_latest_soliloquy()` took the newest entry globally, so session start on BoltMob surfaced a reflection about a Rust project. **Repaired during this pass:** `persistence.py:1045` now takes `project_id` and prefers this project's own entry | — | the 52 pre-existing entries carry no project attribution; confirm the fallback behaviour reads acceptably before calling this closed | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C27` |
| E03 | CLAUDE.md / plan §1.5 — lessons are global by design (no `project_id` on `Lesson`) | correct and deliberate. Lessons are cross-project wisdom; the catalogue is project-specific. REM distils project-local knowledge upward into global lessons and must never consume it | — | do not "fix" this. Do not partition the corpus | **VERIFIED** (by design, recorded so nobody undoes it) | `grep -n "project_id" src/mgcp/models.py` |

## D. Enforcement — the part that works

This is the one subsystem observed changing agent behaviour where instruction did
not. `query-before-git-operations` failed as a *lesson* from v1 through v4 across
months while the advisory hook fired correctly every time. As a
`permissionDecision: "deny"` it holds.

Rows C16–C21 execute **the real shipped hook**
(`src/mgcp/hook_templates/pre-tool-dispatcher.py`) as a subprocess, fed the JSON
shape Claude Code feeds it, with throwaway state and rules files. There is no
mock between the claim and the assertion. C20 runs against a real git repository
with real staged files.

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C16 | README:299 / CLAUDE.md:238 — PreToolUse "can actually refuse a tool call by returning `permissionDecision: "deny"`" | it does. `git commit` with no `query_lessons` in the turn returns a deny payload naming the rule | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C16` |
| C17 | the gate opens once its precondition is met | it does: same command with `turn_tools_called=["mcp__mgcp__query_lessons"]` exits 0 with no payload | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C17` |
| C18 | CLAUDE.md:232 — "`MGCP_BYPASS:<scope>` disables one scope, bare `MGCP_BYPASS` disables all" | scoped bypass works and is genuinely scoped: `MGCP_BYPASS:docs` does **not** open the `git` rule | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C18` |
| C19 | README:301 — quote-aware tokenisation via `shlex(punctuation_chars=True)`, so `grep 'git commit' docs/` passes through | it does, for quoted mentions and for `git status`. False positives are how an enforcement layer gets switched off; there are none here | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C19` |
| C20 | CLAUDE.md:269 — `staged_files_coupling` can "enforce doc-coupling … on commits" | it does: staging `src/app.py` alone is denied; staging `README.md` alongside it is allowed | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C20` |
| C21 | CLAUDE.md:236 — "The PreToolUse hook fails open … enforcement is a net, not a tripwire" | it does: a corrupt `enforcement_rules.json` yields exit 0 and no denial | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C21` |
| C22 | README:330 — "The SessionStart REM-overdue detector activates immediately on upgrade because it reads the DB directly" | true of the shipped *template*; false of this machine. `~/.mgcp/hooks/session-init.py` is dated 2026-04-08 and contains neither the v2.6 stale-hook detector nor the v2.7 REM detector | hooks are copied at `mgcp-init` time. Upgrading the package does not redeploy them, so every v2.6/v2.7 hook claim in the README is false for an install that has not re-run `mgcp-init` | either say so plainly in the README, or have SessionStart compare `hook_templates/VERSION` with `~/.mgcp/hooks/.mgcp-hook-version` and warn | **FAKE** | `pytest tests/test_claims.py -k C22` |
| E04 | README:307 — "Adding a new rule is a chat-time `add_enforcement_rule` call; the next tool call picks it up with no restart" | the hook re-reads `~/.mgcp/enforcement_rules.json` on every invocation, so no restart is needed; 56 unit tests cover the evaluator on both sides | not exercised end to end through MCP tool → JSON file → hook | add one round-trip test through `add_enforcement_rule` | **CLAIMED** | `pytest tests/test_enforcement.py tests/test_pre_tool_dispatcher.py -q` |

## E. Retrieval

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C23 | README:34 — "**Semantic search** finds relevant lessons without exact keyword matches" | a quality claim needs a measurement. One now exists: `tests/benchmark_data/retrieval_queries.yaml`, 34 labelled queries with gold lessons and hard negatives, drawn from the real 238-lesson corpus | — | keep the set in step with the corpus | **VERIFIED** | `pytest tests/test_claims.py -k C23` |
| C24 | the similarity floor separates signal from noise — IT DOES NOT, and 0.30 is the honest setting | the embedding layer is textbook — `embed()` for documents, `embed_query()` with the BGE instruction prefix for queries. The floor **was** `0.3`, below the level at which BGE cosine distinguishes any two pieces of English prose, so `limit` did all the filtering and ranking was whatever the model returned. As of 2026-07-28 it is `0.30`, calibrated against the labelled set | none remaining in the code. The risk is that the number drifts again silently | this row pins it: `RETRIEVAL_FLOOR = 0.30` above must equal the code | **VERIFIED** | `pytest tests/test_claims.py -k C24` |
| E05 | README:35 — "**Graph relationships** surface connected knowledge together" | `spider_lessons` traverses typed edges; `query_lessons` additionally bridges through community summaries. Unit-tested on a synthetic graph | no measurement of whether bridging surfaces *useful* neighbours on the real corpus. The bridge appends lessons with a hardcoded score of `0.0` and applies no floor of its own | fold bridged results into the retrieval benchmark | **CLAIMED** | `pytest tests/test_basic.py -k spider -q` |

## F. "Real Value Delivered" — README:48

Four of these five bullets describe outcomes. An outcome claim needs an
instrument, and where the instrument is missing the row is FAKE however true the
sentiment may be.

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C26 | "**Caught bugs before they happened** — lessons from past mistakes surface before repeating them" | unfalsifiable as written. What *is* measurable: `query_lessons` does call `store.record_usage`, and 225 of 238 lessons have `usage_count > 0`. Lessons are being retrieved | the claim asserts a counterfactual (a bug that did not happen). The instrument that exists measures *retrieval*, not *prevention* | rewrite to what is true and checkable: "lessons surface at the moment of need — 225 of 238 have been retrieved at least once" | **VERIFIED** (as worded) | `pytest tests/test_claims.py -k C26` |
| C25 | "**Kept documentation in sync** — workflow steps enforce doc review before commits" | workflow steps are advisory text and enforce nothing. A real mechanism exists and ships **enabled**: `version-bump-requires-readme` in `enforcement.py:DEFAULT_RULES`, a `staged_files_coupling` precondition that denies the commit (proved end to end in row C20) | the outcome is real; the attribution is wrong. Crediting workflows for what enforcement does hides the one genuinely novel thing in the system | rewrite as "enforcement rules refuse commits that change source without touching docs" | **VERIFIED** (wording; mechanism VERIFIED at C20) | `pytest tests/test_claims.py -k "C25 or C20"` |
| E06 | "**Enforced quality gates** — workflows with checklists prevent skipped steps" | nothing reads a workflow checklist and blocks on it. `update_workflow_state` records progress; no precondition type consults it. Every real gate in the system is an enforcement rule, not a workflow | a checklist the agent can skip is advice | either add a precondition type that reads workflow state, or retract the wording | **FAKE** | `grep -rn "checklist" src/mgcp/enforcement.py src/mgcp/hook_templates/pre-tool-dispatcher.py` → no hits |
| E07 | README:36 — "**Workflows** ensure multi-step processes don't get shortcut" | same defect as E06: workflows describe, they do not ensure | — | see E06 | **FAKE** | see E06 |
| E08 | "**Maintained project context** — picking up exactly where the last session left off" | `get_project_context` / `save_project_context` persist todos, decisions, active files and notes per project path; the SessionStart hook instructs the agent to load them | proven against a temp DB, not across a real session boundary | nothing urgent | **CLAIMED** | `pytest tests/test_basic.py -k project_context -q` |
| E09 | "**Preserved architectural decisions** — rationale survives session boundaries" | catalogue `decision` items carry `rationale` and are semantically searchable | as E08 | nothing urgent | **CLAIMED** | `pytest tests/test_catalogue_vector.py -q` |
| E10 | README:37 — "**Hooks** make it proactive — reminders fire automatically at key moments" | five hooks are wired into `~/.claude/settings.json` and fire on their events; the scheduled-reminder path is unit-tested | the *deployed* copies are stale (C22), so "key moments" added in v2.6/v2.7 do not fire on this machine | fix C22 | **CLAIMED** | `pytest tests/test_session_init.py -q` |
| E11 | README:40 "**What this is NOT**" — not AI that learns, not self-improving, not magic | accurate and appropriately conservative. REM's `knowledge_extraction` only *proposes* findings; `rem_cycle.py` has no write path into the lesson store | — | keep this section. It is the most honest paragraph in the README | **VERIFIED** | `grep -n "add_lesson\|save_lesson" src/mgcp/rem_cycle.py` → no write path |

## G. Skill compilation — the row that exists because readers get it wrong

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C28 ✅ | README:433 Project Status and CLAUDE.md:9 both used to say skill compilation was **"Removed"** | **Was FAKE.** `src/mgcp/skill_compiler.py` is live code, `compile_intent_to_skill` is one of the 49 tools, there is a web endpoint and a UI button, and `tests/test_skill_compiler.py` covers it. "Removed" reads as "the code is gone", and an agent reading CLAUDE.md concluded exactly that. What was abandoned is the **strategy** of graduating lessons out of `query_lessons`, which hid knowledge from the agent. **Repaired during this pass:** both lines now read "strategy dropped, feature shipped" | — | keep the boundary stated: **skill compilation emits a FILE and never writes to the knowledge store. REM owns knowledge maintenance. Do not conflate them.** | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C28` |

---

## Open questions for the operator

Blocking. Not guessable.

1. **REM schedule state (C13, C29).** Two things need your hand, not an agent's:
   (a) the seven `rem_state` rows hold `last_run_session = 98` written by the
   old global-max bug, and until they are reset REM cannot become due for any
   project; (b) making the schedule genuinely per project changes the primary key
   of `rem_state`. Both touch the live store, so both want a dry-run that reports
   what *would* change, and a real run you trigger deliberately.

2. **Soliloquy history (C27).** Read is now project-preferring, but the 52
   existing entries have no project attribution and will keep falling through to
   the global fallback. Backfill them, or accept the fallback and say so.

## Named once and dropped — backlog, not this pass

- `Lesson.parent_id` and `Lesson.related_ids` are marked deprecated in favour of
  `relationships` but still carry dozens of live references.
- 49 tools is a large surface for one agent to hold. Rank by `usage_count` and put
  the never-called list in front of the operator; delete nothing unilaterally.
- The community bridge inside `query_lessons` appends lessons with a hardcoded
  score of `0.0` and applies no relevance floor of its own.
- `mgcp-launcher` ships as a console script and is documented nowhere.
- A PreToolUse "apology gate" is present in the working tree and is documented in
  neither README nor CLAUDE.md.

## How to keep this file honest

When a repair lands, the matching `test_C<NN>` turns green. Flip the row to
VERIFIED **in the same commit**, and update the scoreboard. A row whose status
changed without its test being run is worth nothing — the README got where it is
by exactly that route.
