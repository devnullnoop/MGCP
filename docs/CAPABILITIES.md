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
| VERIFIED | 36 |
| CLAIMED | 3 |
| FAKE | 0 |
| RETRACTED | 2 |
| **total** | **41** |

These counts are checked against the rows below by
`test_ledger_scoreboard_matches_its_rows`, because this table had already
drifted to 20 / 7 / 13 while the rows said 31 / 7 / 0 — nothing read it. A
summary nobody checks fails the same way a claim nobody tests does, which is
the thing this document exists to stop.

**The suite no longer reaches the operator's data.** `web_server.py` calls
`LessonStore()` with no path, and `test_api_ui_integration` imports that app,
so a plain `pytest` run opened `~/.mgcp/lessons.db` and `~/.mgcp/qdrant`
directly. That was survivable while it only ever read — it showed up as five
tests that failed whenever the MCP server held the Qdrant lock, which everyone
had learned to read as "pre-existing." It stopped being survivable the moment a
repair migration was added to store open: a test run rewrote the operator's REM
schedule rows. `conftest.py` now points `MGCP_DATA_DIR` at a throwaway
directory before anything imports `mgcp`, and exports `MGCP_LIVE_DATA_DIR` for
the rows here that inspect the real install on purpose, read-only. The full
suite is now green — 885 passed, 0 failed, 0 errors — and the live database is
byte-identical before and after a run.

Measured 2026-07-29 against `0e656c1`: `tests/test_claims.py` → **37 passed**
(`C19` is parametrised three ways, `C16`'s bypass-shapes row four more). **No
FAKE rows remain**, which was the finish line the repair plan set. Re-run it;
do not trust this paragraph.

The two RETRACTED rows are E06 and E07: the README claimed workflows *enforce*
quality gates and *ensure* steps are not shortcut, and they do neither. The
alternative to retracting was a precondition type that reads workflow state —
a new subsystem, which the repair plan's §5 rules out. The outcome was real
all along; the attribution was wrong, and it was hiding the mechanism that
does the work.

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
| C04 ✅ | CLAUDE.md:88 — used to say "`server.py` - MCP server with 37 tools" | **Was stale by 12** — not updated when v2.2–v2.4 added tools. **Repaired** (commit `31c5ea5`): CLAUDE.md:88 now reads 49, agreeing with the code. This row itself then rotted the same way — it kept quoting the old sentence and presenting "37 → 49" as pending long after the fix landed, caught by the 2026-07-29 verification sweep. The sentence is the claim, including this one | — | nothing | **VERIFIED** *(repaired; row prose corrected 2026-07-29)* | `pytest tests/test_claims.py -k C04` |
| C05 | README:353 Commands table — 9 CLI commands | all 9 are `[project.scripts]` entries and every `module:function` target imports and resolves | `mgcp-launcher` ships undocumented (harmless) | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C05` |
| C06 | README:367 API table — 6 endpoints | `/api/health`, `/api/lessons`, `/api/projects`, `/api/graph`, `/ws/events` all have routes; `/docs` is FastAPI's own | it documents 6 of ~33 routes, which is a summary, not a lie | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C06` |
| C07 | README:341 / CLAUDE.md:228 — "Current hooks" tables | all 5 named hook scripts exist in `src/mgcp/hook_templates/` | — | nothing | **VERIFIED** | `pytest tests/test_claims.py -k C07` |
| C08 ✅ | CLAUDE.md:9 — "**Status**: v2.1.0" | **Was FAKE** — `pyproject.toml` = 2.1.0 but `mgcp/__init__.py` = 2.0.0, so an install reported whichever source it happened to read. **Repaired during this pass:** `__init__.py` moved to 2.1.0, agreeing with `pyproject.toml` and CLAUDE.md | `hook_templates/VERSION` (2.10 as of this writing) is deliberately *not* one of these sources — it versions the deployed hook payload and gates hook auto-upgrade, so it moves when a hook changes, not when the package does | nothing | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C08` |
| C09 ✅ | `rem_run` docstring — "Options: staleness_scan, duplicate_detection, community_detection, knowledge_extraction, context_summary" | **Was FAKE** — `DEFAULT_SCHEDULES` has 7; `intent_calibration` and `action_effectiveness` were omitted, so the agent could not name two operations that exist, including the one that closes the REM growth loop CLAUDE.md:240 advertises. **Repaired during this pass:** both added to the `Options:` list. *(Later the same day, `action_effectiveness` was deleted at the operator's call — it read a table only tests ever wrote — so the list is now 6. The test compares the docstring against `DEFAULT_SCHEDULES` rather than a fixed number, which is why it stayed green through both changes.)* | — | nothing | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C09` |
| C10 ✅ | `add_enforcement_rule` docstring — precondition `type` ∈ {`tool_called_this_turn`, `tool_not_called_this_turn`, `staged_files_coupling`} | **Was FAKE** — `enforcement.py` also accepts `tool_input_glob`, added by in-flight v2.8 work without the docstring; the agent is the only caller and could not discover it. **Repaired during this pass:** the docstring now lists it with its `field` / `deny_globs` arguments, and CLAUDE.md's precondition set matches | — | nothing | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C10` |
| C30 | the FastAPI self-description at `/docs` / `/openapi.json` presents itself as the API's endpoint map | it advertised `/api/compiled-skills` and a `/skills` UI page — **routes that never existed**; both returned 404 when used as documented. A third ghost, `/api/projects/{id}/catalogue`, was the wrong parameter template for the real `/api/projects/{project_id}/catalogue` — caught by this row's own test the day it was written | the description block was prose nobody parsed, so it rotted independently of the routes | description now names only real routes, and the test extracts every backtick path from it and resolves each against `app.routes` | **VERIFIED** *(repaired 2026-07-29)* | `pytest tests/test_claims.py -k C30` |

## B. REM — a subsystem that reports success while doing nothing

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C11 ✅ | README:237 — "REM runs periodic consolidation to keep the knowledge base healthy without manual curation", on per-operation schedules ("every 5 sessions", "every 10", fibonacci, logarithmic) | **Was FAKE.** `server.py:2040` set `session_number = max((p.session_count for p in projects), default=1)` — the maximum across **every project on the machine**. `is_due()` opens `if current_session <= last_run_session: return False`, and every operation had recorded `last_run_session = 98` (this repo's own count). Five `rem_run` calls in one BoltMob session all returned "Operations run: none." **Repaired during this pass:** `server.py:2068` now reads `session_number = project.session_count` | — | see C13 and C29 — the code fix alone does not revive REM | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C11` |
| C12 ✅ | `rem_status` docstring — "Show REM schedule state: what ran when, what's due next" | **Was FAKE** (same `max()` at `server.py:2118`, so the "Current Session: N" header named the busiest project on the machine). **Repaired during this pass:** `server.py:2155` now reads `current = project.session_count`, and `get_status()` now returns `is_due` computed with the same predicate `rem_run` uses, so display and execution cannot disagree. *(2026-07-29: the published due DATE was still a second implementation — `next_due_session` returned the next multiple of the interval while `is_due` measures sessions elapsed, so the table said 100 for an operation that does not fire until 103. It is now derived by asking `is_due`, and stored rows written by the old arithmetic are repaired on store open.)* | — | nothing | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C12` |
| C13 ✅ | README:319 v2.7 — "SessionStart visibility layer (**always on**)" injects a "REM Operations Overdue" block | **Passes on the "ran recently" branch, and credit belongs to C11, not to this pass.** REM last ran 2026-07-29 against the live store, so the disjunction the test asserts (ran within 30 days **or** currently flagged) holds on the first arm. This pass additionally scoped the detector's query by `project_id`, so it can no longer report a neighbouring project's schedule as this project's | **Residual, deliberate:** a project with *no* `rem_state` rows is never flagged overdue, even though every operation is due for it. Flagging every fresh project at session 1 would be pure noise, and `rem_run` makes them due anyway — the detector is a watchdog for lapsed schedules, not for absent ones | nothing; re-check if REM goes quiet again and the flag arm has to carry it | **VERIFIED** *(passes via recent run; scoping repaired this pass)* | `pytest tests/test_claims.py -k C13` |
| C29 ✅ | README:237 — schedules are per operation, per project | **Was FAKE.** `rem_state` was declared `operation TEXT PRIMARY KEY` — **one row per operation for the entire machine** — so a `last_run_session` of 98 written by this repo blocked BoltMob (session 36) forever, and whichever project ran REM next overwrote everyone's schedule. Measured before the fix: `rem_status` for BoltMob reported "Last Run: Session 98" against its own session 36. **Repaired during this pass:** the table is keyed `PRIMARY KEY (project_id, operation)`, `get_rem_state`/`update_rem_state` require a `project_id`, and `RemEngine` carries one | — | nothing | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C29` |
| E01 ✅ | CLAUDE.md — "Phase 7: Feedback Loops — Complete (REM cycle engine …)" | **The loop has now closed on the real store.** All six operations ran against `~/.mgcp/lessons.db` on 2026-07-29 under the (project_id, operation) key, with real finding counts: staleness_scan 81, duplicate_detection 0, community_detection 1, knowledge_extraction 16, context_summary 11, intent_calibration 2. Unit coverage: 12 tests in `test_rem_cycle.py`, 58 in `test_rem_scheduling.py` (the earlier 17/15 counts predate the action_effectiveness deletion and the scheduling-agreement suite) | — | nothing — the row that said "nothing proves the loop closes on a real store" stopped being true the day REM was unblocked | **VERIFIED** *(2026-07-29 run recorded here, per this row's own what-to-do)* | `sqlite3 "file:$HOME/.mgcp/lessons.db?mode=ro" "SELECT operation, last_run_result FROM rem_state"` |
| E02 | README — "The schedules are configurable but the defaults work well in practice" | every default WAS exercised 2026-07-29 (first full cycle since May), so the claim is now falsifiable — but one cycle on one project cannot show "work well in practice" | needs cycles over time, not code | the deletion clock this row proposed is now armed: duplicate_detection ran and found 0. If it finds nothing for 20 sessions it is a deletion candidate | **CLAIMED** | `sqlite3 "file:$HOME/.mgcp/lessons.db?mode=ro" "SELECT operation, last_run_result FROM rem_state"` |

## C. Data integrity and project isolation

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C14 | `models.SanitizedModel` — "strips tool-call XML from any string field" | it **neutralises** rather than strips: `<invoke>` / `<parameter>` become `‹invoke›` / `‹parameter›`. The safety property — stored text cannot be replayed as a tool call — holds, both on construction and on direct attribute assignment | the summary line's verb; the docstring body is accurate two lines later | none; the property is real | **VERIFIED** | `pytest tests/test_claims.py -k C14` |
| C15 ✅ | README:32 — accumulated lessons are the value | **Was FAKE** — lessons in the live store carried serialised tool-call envelope fragments inside `action` / `rationale`, stored and then **embedded as content**, degrading retrieval as well as display. Worse than recorded: the corruption also sat in 9 `project_contexts`, and because the v2.5 write guard validates the whole context on write-back, **`add_catalogue_item` was failing outright for those 9 projects** — measured 2026-07-29, live rejected / repaired copy accepted | — | nothing | **VERIFIED** *(operator ran `clean_tool_call_envelopes.py --apply --yes-live` 2026-07-29: 552 records repaired, 15 lessons + 153 catalogue items re-embedded across 9 projects; live re-report now says `Affected records: 0`)* | `pytest tests/test_claims.py -k C15` |
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
| C22 ✅ | README:330 — "The SessionStart REM-overdue detector activates immediately on upgrade because it reads the DB directly" | **Was FAKE** — true of the shipped *template*, false of this machine: `~/.mgcp/hooks/session-init.py` was dated 2026-04-08 and contained neither the v2.6 stale-hook detector nor the v2.7 REM detector. Hooks are copied at `mgcp-init` time, so upgrading the package never redeployed them | **The claim is still conditional on a redeploy** — "activates immediately on upgrade" is only true once `mgcp-init --force` runs. The install now self-reports drift: SessionStart compares `hook_templates/VERSION` against `~/.mgcp/hooks/.mgcp-hook-version`, and `init_project` auto-upgrades when the marker is behind | nothing | **VERIFIED** *(operator ran `mgcp-init --force` 2026-07-29; all five deployed hooks now byte-identical to their templates, marker since redeployed at 2.10)* | `pytest tests/test_claims.py -k C22` |
| E04 | README:307 — "Adding a new rule is a chat-time `add_enforcement_rule` call; the next tool call picks it up with no restart" | the hook re-reads `~/.mgcp/enforcement_rules.json` on every invocation, so no restart is needed; 115 unit tests (53 + 62 across both files) cover the evaluator on both sides | not exercised end to end through MCP tool → JSON file → hook | add one round-trip test through `add_enforcement_rule` | **CLAIMED** | `pytest tests/test_enforcement.py tests/test_pre_tool_dispatcher.py -q` |

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
| C25 | "**Kept documentation in sync** — workflow steps enforce doc review before commits" | workflow steps are advisory text and enforce nothing. A real mechanism exists and ships **enabled**: `version-bump-requires-readme` in `enforcement.py:DEFAULT_RULES`, a `staged_files_coupling` precondition that denies the commit (proved end to end in row C20) | the outcome is real; the attribution is wrong. Crediting workflows for what enforcement does hides the one genuinely novel thing in the system | **Applied** — README:53 now reads "an enforcement rule refuses commits that change source without touching docs", crediting the mechanism proved at C20 | **VERIFIED** (wording corrected; mechanism VERIFIED at C20) | `pytest tests/test_claims.py -k "C25 or C20"` |
| E06 ⌫ | "**Enforced quality gates** — workflows with checklists prevent skipped steps" | nothing reads a workflow checklist and blocks on it. `update_workflow_state` records progress; no precondition type consults it. Every real gate in the system is an enforcement rule, not a workflow | a checklist the agent can skip is advice | **Retracted, not implemented.** The alternative was a precondition type that reads workflow state — a new subsystem, and the repair plan's §5 says do not add features. The README line now credits the PreToolUse rule that actually denies the call | **RETRACTED** *(README, commit "Stop crediting workflows for what enforcement does")* | `grep -rn "checklist" src/mgcp/enforcement.py src/mgcp/hook_templates/pre-tool-dispatcher.py` → no hits, and the claim no longer appears in README |
| E07 ⌫ | README:36 — "**Workflows** ensure multi-step processes don't get shortcut" | same defect as E06: workflows describe, they do not ensure | — | retracted with E06; README:36 now reads "sequence … guidance, not a gate" | **RETRACTED** *(README, commit "Stop crediting workflows for what enforcement does")* | see E06 |
| E08 ✅ | "**Maintained project context** — picking up exactly where the last session left off" | **Proven across real session boundaries on the live store**: the MGCP context is at session_count 99 with 128 versioned `context_history` snapshots spanning sessions 63–99 (2026-02-08 → 2026-07-29), and 7 other projects show the same pattern (BoltMob 36 sessions, MoneyTree 14, …). Context has demonstrably persisted and been picked up across dozens of real sessions | — | nothing | **VERIFIED** *(live-store evidence recorded 2026-07-29)* | `sqlite3 "file:$HOME/.mgcp/lessons.db?mode=ro" "SELECT project_name, session_count FROM project_contexts ORDER BY session_count DESC LIMIT 5"` |
| E09 ✅ | "**Preserved architectural decisions** — rationale survives session boundaries" | **Live evidence**: 11 projects carry catalogue `decision` items with populated rationale (MGCP 16, BoltMob 31, …), including projects last touched in Feb–Mar 2026 whose rationale reads back intact in July — months of survived session boundaries. Semantic search over them is covered by `test_catalogue_vector.py` (17 passed) | — | nothing | **VERIFIED** *(live-store evidence recorded 2026-07-29)* | `pytest tests/test_catalogue_vector.py -q` |
| E10 ✅ | README — "**Hooks** make it proactive — reminders fire automatically at key moments" | five hooks wired in `~/.claude/settings.json` (exactly once each), all five deployed copies **byte-identical** to the shipped templates, marker 2.10 == `hook_templates/VERSION`, and `~/.mgcp/workflow_state.json` was written by the hooks the same day — they demonstrably fire live. The former gap ("deployed copies are stale, see C22") closed when C22 did | — | nothing | **VERIFIED** *(deployment verified byte-level 2026-07-29)* | `for f in ~/.mgcp/hooks/*.py; do cmp -s $f src/mgcp/hook_templates/$(basename $f) && echo same: $(basename $f); done` |
| E11 | README:40 "**What this is NOT**" — not AI that learns, not self-improving, not magic | accurate and appropriately conservative. REM's `knowledge_extraction` only *proposes* findings; `rem_cycle.py` has no write path into the lesson store | — | keep this section. It is the most honest paragraph in the README | **VERIFIED** | `grep -n "store\.save\|store\.add\|store\.delete" src/mgcp/rem_cycle.py` → no hits (the only store call is `update_rem_state`; the two `graph.add_lesson` hits are in-memory NetworkX inserts, not persistence) |

## G. Skill compilation — the row that exists because readers get it wrong

| # | What we say | What is actually true | Where the gap is | What to do | Status | Command |
|---|---|---|---|---|---|---|
| C28 ✅ | README:433 Project Status and CLAUDE.md:9 both used to say skill compilation was **"Removed"** | **Was FAKE.** `src/mgcp/skill_compiler.py` is live code, `compile_intent_to_skill` is one of the 49 tools, there is a web endpoint and a UI button, and `tests/test_skill_compiler.py` covers it. "Removed" reads as "the code is gone", and an agent reading CLAUDE.md concluded exactly that. What was abandoned is the **strategy** of graduating lessons out of `query_lessons`, which hid knowledge from the agent. **Repaired during this pass:** both lines now read "strategy dropped, feature shipped" | — | keep the boundary stated: **skill compilation emits a FILE and never writes to the knowledge store. REM owns knowledge maintenance. Do not conflate them.** | **VERIFIED** *(repaired during this pass)* | `pytest tests/test_claims.py -k C28` |

---

## Open questions for the operator

Blocking. Not guessable.

1. ~~**REM schedule state (C13, C29).**~~ **Closed.** `rem_state` is now keyed
   `(project_id, operation)`. The seven global rows are not reset and not
   dropped: on first open of an existing store they are attributed to the
   project with the highest `session_count`, because that is literally whose
   clock the old `max()` scheduler was reading. Every other project starts with
   no row, which is the truth — REM has never been scheduled on its own clock —
   and is therefore immediately due. The rebuild runs automatically rather than
   on your trigger, because the alternative was a hard SQL error on the next
   `rem_run` against an un-migrated store; it is idempotent and logs the
   attribution at WARNING. **It has not yet run against your live store** — that
   happens when the MCP server next restarts.

2. **Soliloquy history (C27).** Read is now project-preferring, but the 52
   existing entries have no project attribution and will keep falling through to
   the global fallback. Backfill them, or accept the fallback and say so.

## Named once and dropped — backlog, not this pass

*Added by the 2026-07-29 full-verification sweep (10 agents, all below the bar):*

- `mgcp-migrate --dry-run` exits 1 instead of previewing when Qdrant data already exists; `--dry-run --force` previews safely (the dry-run return precedes the rmtree). Let dry-run bypass the guard or document the pairing.
- `mgcp-backup --restore` prints "Restored to: X" with exit 0 even when the archive's top dir wasn't named `.mgcp` and nothing landed at X (undocumented flag combination only; the documented round-trip works).
- `backup.py` is the one store-touching module that ignores `MGCP_DATA_DIR` (argparse default is hardcoded `~/.mgcp`).
- Line-number citations in ledger cells and README prose drift on every edit (C11/C12/C27 anchors, ~14 "What we say" citations, README's `init_project.py:720`). The quoted sentences all still exist; only the numbers moved. Prefer symbol/heading anchors when next touched.
- E04 still needs the one test it names: drive `add_enforcement_rule` → JSON file → real hook subprocess. And a denied tool call currently leaves no trace — a one-line append-only deny log would make D-row claims upgradeable from recorded evidence.

- ~~`Lesson.parent_id` and `Lesson.related_ids` are marked deprecated~~ — done
  2026-07-29. `related_ids` is gone (every live id was already in
  `relationships`); `parent_id` stays because it is the category hierarchy and
  was mislabelled, not deprecated. See the plan's §4 outcome table.
- ~~Rank 49 tools by `usage_count`~~ — **not computable.** `usage_count` is a
  `Lesson` field; nothing counts tool invocations, and telemetry logs only 7
  lesson-centric event types. The list cannot be produced without first adding
  per-tool counting, which is a feature. Statically, all 49 are referenced
  outside `server.py`; the four enforcement-rule CRUD tools have docs but no
  test, hook, or UI.
- ~~`action_effectiveness` (REM) cannot produce a finding in real use~~ —
  **deleted 2026-07-29** at the operator's call, along with the whole
  `rem_actions` apparatus it was the only production consumer of: the table,
  the `RemAction` model, five store methods, `capture_lesson_baseline`, and the
  migration that created the table. REM now has 6 operations.
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
