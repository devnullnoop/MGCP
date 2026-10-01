# Changelog

All notable changes to MGCP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.0.0] - 2026-10-01

Multi-session access. Three workstreams, each verified against the real thing
rather than a mock, and each independently droppable — A and C landed first
because shipping concurrency before write-safety would have turned a rare silent
data loss into a common one.

The package version jumps 2.1.0 -> 3.0.0. It had drifted far behind the feature
line and, worse, sorted *below* it: `2.1.0 < 2.13` as a version string while
2.13 was the newer release. The hook/feature line stays at 2.13 in
`src/mgcp/hook_templates/VERSION` because v3 changes no hook behaviour.

### Added (v3.0 workstream B — one shared embedding model per machine)
- **`mgcp-embed`, a shared embedding daemon.** `BAAI/bge-base-en-v1.5` is ~448 MiB resident and every process that embedded anything loaded its own copy: each MCP session, the dashboard on its first write, every CLI. The daemon loads it once and answers over a unix domain socket in the MGCP data directory — not TCP, so it is unreachable off-box and needs no authentication.
- **Measured, three real client processes against one daemon:** total RSS **1,344.6 MiB -> 565.4 MiB**, a **779 MiB (58%) saving**, and each additional session now costs **39 MiB instead of 448**. Cold start for the second and subsequent sessions fell from ~2,100 ms to ~65 ms, because they no longer load the model at all.
- **The latency estimate in the design doc was wrong by two orders of magnitude** and was corrected by measuring rather than by reasoning. It predicted 1-3 ms for the round trip. Actual: **0.009 ms** on a persistent connection, 0.060 ms connecting per call, and a `ping` through the fully assembled daemon round-trips in **0.014 ms**. End-to-end `embed` is 7.6-8.5 ms through the daemon against 7.8-9.5 ms in process over three alternating rounds — indistinguishable, and occasionally *faster*, since a client that never imports torch is not holding its thread pool. An early reading of 16 ms was noise from six consecutive model loads; it was chased down rather than published.
- **Vectors are bit-identical across the two paths, not merely close.** The wire carries raw little-endian float32, which is the dtype `encode` already produces, and the daemon computes by calling the same `embed_in_process` / `embed_query_in_process` / `embed_batch_in_process` functions a local caller would. `tests/test_embedding_daemon.py` asserts exact equality, so the paths cannot silently drift and the nine-month retrieval-quality baseline stays comparable.
- **A dead daemon costs speed, never correctness.** `embed` / `embed_query` / `embed_batch` keep their signatures; if no daemon answers, or it idles out mid-session, or it returns an error frame, or the connection breaks, the caller falls back in-process and never sees an exception. One reconnect is attempted first, because a persistent connection to a daemon that has idled out presents as a broken pipe rather than a failure to connect.
- **Rejected: Qdrant's own FastEmbed server-side inference**, which looks tidier. It changes which model produces the vectors, so the whole corpus would need re-embedding and the retrieval baseline measured over nine months (median top-1 0.691) would stop being comparable.
- **Lazy start with an idle timeout.** The first client that finds no socket spawns the daemon, detached so it outlives the session that needed it first, and waits for readiness; after 900 s idle it exits, so a one-off CLI does not leave 448 MiB resident all day. `MGCP_EMBED_DAEMON=0` disables the daemon entirely, `MGCP_EMBED_AUTOSTART=0` uses one that is running but never starts one, and `MGCP_EMBED_SOCKET` overrides the path. The test suite sets `MGCP_EMBED_DAEMON=0`, so an ordinary `pytest` neither forks a 448 MiB process nor changes its answers based on whether a daemon happens to be up.
- **Start races resolve to success.** `bind()` attempts and tolerates rather than checking first: two processes starting together both see no socket, and the loser has nothing to report because the daemon it wanted now exists. This is the same mistake that shipped as a 409 in Qdrant server mode one commit earlier, applied in advance this time.
- **A socket file left by a killed daemon is indistinguishable on disk from a live one**, so `bind()` pings before deciding; only a socket that nothing answers is removed and reclaimed.
- **Found by its own tests: an over-long socket path crashed instead of degrading.** `sockaddr_un.sun_path` is 104 bytes on macOS and 108 on Linux. pytest's `tmp_path` exceeds it, so `bind()` died with a bare `AF_UNIX path too long` and a client then waited the full 30 s startup timeout before falling back — and a sufficiently deep `MGCP_DATA_DIR` does the same in production. The limit is now checked up front: the daemon raises a message naming the byte count and the override, and the client skips the spawn and falls back immediately.
- `/api/health` reports the embedding path (`daemon` or `in-process`, with the reason and the socket). Whether a process holds its own copy of BGE is the entire substance of this workstream and was otherwise invisible.
- 25 new tests in `tests/test_embedding_daemon.py`, including `test_two_real_processes_share_one_daemon` — two separate interpreters against one daemon, asserting neither ever loaded a model of its own. A threaded daemon proves the protocol but shares the parent's already-loaded model, so it cannot prove the saving. Suite: **933 passed**, 1 skipped (needs a Qdrant server), 1 xfailed.


### Fixed (v2.13 — mechanisms that were wired to nothing)
Found by a whole-module review (10 readers over disjoint slices; 137 findings, each put to two refute-by-default verifiers; 121 upheld). Every item below is a measured failure with a reproduction, not a cleanup.
- **The test-suite exit hang was never actually fixed** (v2.12 claimed it). Found while trying to run the suite for this review, not by the review itself. `pytest tests/test_failure_recovery.py` printed `28 passed in 3.1s` and then never exited; a `faulthandler` dump showed the main thread parked in `threading._shutdown` with two live `aiosqlite _connection_worker_thread`s. aiosqlite's worker thread is **non-daemon** (its `core.py` sets no `daemon` flag), so one orphan blocks interpreter shutdown forever. The v2.12 hook walked `_live_stores` and stopped connections sitting in each store's `_pool` — but the leak has a different origin: when `executescript(SCHEMA)` raises on a corrupt or unreadable database, the connection just opened in `_acquire_conn` is neither pooled nor closed. `test_failure_recovery.py` is the file that deliberately corrupts databases, which is why it and not the others hung. `_acquire_conn` now closes the connection if initialisation fails, and every opened connection is tracked so the exit hook can stop one that no pool is holding. Reproduction (a 2 KB file of garbage named `*.db`): hung indefinitely before, exits in 1s after; the full suite now runs to completion — 866 passed, 1 xfailed, exits in 60s. Regression test `tests/test_basic.py::TestProcessExitsAfterStoreUse` fails on the unfixed code with the diagnosis in its message.
- **The apology gate's contest exit had never opened the gate.** `pre-tool-dispatcher.py` requires `turn_apology_adjudication.session_id` to equal the harness `session_id` exactly, but `adjudicate_apology_gate` accepted it as a caller argument and nothing tells the model that value — while the gate is armed it cannot even read the transcript to find it. Live `gate_audit.jsonl`: 93 denials with harness UUIDs, 16 adjudications with `""` or an invented `session_...` id, intersection empty. UserPromptSubmit now records `turn_session_id`; the tool reads it and the parameter is deleted. `tests/test_pre_tool_dispatcher.py::test_sessionless_adjudication_is_legacy_scoped` had been asserting that the only *passing* combination was the one the harness never produces.
- **`schedule_reminder` / `update_workflow_state` wrote to a file no hook reads.** `reminder_state.py` wrote `~/.mgcp/reminder_state.json`; all four hooks read `~/.mgcp/workflow_state.json` and `reminder_state.py` did not honour `MGCP_STATE_FILE` at all. Both files were live on disk with divergent `current_call_count` (0 vs 3) — the channel had been inert since 2026-02-10. Now resolved identically to the hooks. `reset_state` merges rather than replaces, because that file also carries `turn_tools_called` / `turn_bypass_scopes` / `turn_apology_adjudication`; replacing it mid-turn would re-shut the apology gate and fail every `tool_called_this_turn` precondition.
- **REM `duplicate_detection` could not run in-process and reported health it never measured.** `find_duplicates` constructed its own `QdrantVectorStore`; local-mode Qdrant allows one client per path (the constructor says so), so inside the MCP server it always raised and the `except` returned `[]` — which renders as "Knowledge base looks healthy". `find_duplicates` now takes `store`/`vector_store`, `RemEngine` passes its live instances, and a scan that cannot run emits a finding saying so instead of a clean bill of health.
- **`context_history` grew one row per write instead of per session.** 1,315 rows for 212 distinct `(project_id, session_number)` pairs on this repo's store — ~9 per session — while `_context_summary` gates on `len(history) >= 10` and describes the rows as "snapshots spanning sessions", and `_knowledge_extraction` reasons in "pending for N sessions". Now `ON CONFLICT(project_id, session_number) DO UPDATE` (last write of a session wins; `catalogue_delta` is COALESCEd so an intra-session catalogue change is not erased by a later no-change save). An idempotent migration collapses existing rows to `MAX(id)` per pair and then creates the unique index — deliberately in `_run_migrations`, not `SCHEMA`, because `executescript(SCHEMA)` runs first and creating the index there would raise `IntegrityError` and refuse to open any existing store. Verified on a copy of the live 14.4 MB database: 1,315 → 212 rows, `integrity_check ok`, 14.4 MB → 3.5 MB after `VACUUM`. The migration logs the `VACUUM` recommendation rather than rewriting the file unannounced during a store open.
- **`spider()` was nondeterministic and dropped nodes inside the depth limit.** `get_related` returned `list(set)`, whose iteration order over strings varies between processes, and `spider` converts neighbour order into which nodes exceed the depth limit; separately, a node first reached beyond the limit was added to `visited`, so a later shallower path returned early and its in-limit children were never traversed. Neighbours are now `sorted()` and traversal tracks best-depth per node. Regression test fails on the old code with `leaf` absent.
- **A corrupt `enforcement_rules.json` was silently replaced.** `load_config` returned `DEFAULT_RULES` on `JSONDecodeError`/`ValidationError` for a file that *exists*, and every write tool then saved the substitution over the user's rules — which the hook was still enforcing from that same file. Missing file still yields defaults; an unparseable one raises and the calling tool reports it.
- **Bash error detection never saw stderr.** `_extract_output` preferred `stdout` and returned early, so a command failing with empty stdout and a traceback or `fatal:` on stderr read as clean. It now serialises the whole `tool_response`.
- **`get_project_context` incremented `session_count` on every call**, not once per session — inflating the clock that REM's cadence and the SessionStart overdue detector both read, so operations came due early and warned about work that was not due. Guarded on `last_session_id`, which the next line already writes.
- **`mgcp-migrate --dry-run` exited non-zero without previewing** on any install that already had Qdrant data (i.e. every current one): the `has_qdrant and not force` guard returned before the dry-run block. A preview writes nothing and no longer needs `--force`.
- **`update_workflow_state`'s "new workflow resets step tracking" branch was unreachable** — `state["active_workflow"]` was assigned before being compared against, so switching workflows kept the previous one's `steps_completed` and `workflow_complete`.

### Fixed (v2.13 — telemetry ignored the sandbox)
- **`telemetry.py` hardcoded `~/.mgcp/telemetry.db` and never read `MGCP_DATA_DIR`**, while `lessons.db` and both Qdrant stores honoured it. Any process pointed at a throwaway data directory therefore still read *and wrote* the operator's live telemetry. Caught when a dashboard seeded with 22 synthetic lessons rendered 1,094 real queries: the corpus came from the sandbox and the telemetry came from the live store. The consequence is a disclosure risk, not just a wrong number — it is how real query text reaches a screenshot taken from a store that was supposed to contain nothing real. Now resolved by `get_default_telemetry_path()`, matching `persistence.get_default_db_path()`. Verified no test run had polluted the live store (5 events today, all genuine). `tests/test_claims.py::test_every_store_honours_MGCP_DATA_DIR` pins all three stores at once and fails on the old constant.

### Changed (v2.13 — documentation images are synthetic)
- **Every README screenshot is now rendered from a seeded synthetic store.** The first capture of the new UI was taken from the live store and staged for commit; a live MGCP store carries query text, absolute paths from unrelated repositories, other project names, gate-audit transcripts and the soliloquy journal, so committing such an image publishes all of it. The seed generates its own lessons, projects, telemetry, gate audit and REM clocks, and the captures are audited for live project names, paths and lesson ids before they land. Recorded as the lesson `screenshots-use-synthetic-data` so the next capture inherits it rather than relying on someone remembering.

### Added (v2.13 — the dashboard rebuilt as an instrument panel)
- **The dashboard and the MCP server can now run at the same time.** `web_server` opened Qdrant eagerly at startup, so it could not start at all while an MCP server held the lock — despite none of the eight analytics views needing vectors. Every view reads `lessons.db`, `telemetry.db` and `gate_audit.jsonl`; only lesson create/update/delete touch Qdrant. The store is opened on first use (`get_vector_store()`), and those few routes return 503 naming what holds the lock instead of the server refusing to boot. Verified with both processes live: `/api/signal`, `/api/effectiveness` and `/api/gate-audit` all 200 while the MCP server held the lock, and a lesson PUT returned a 503 that says why.
The eight pages were deleted, not migrated: each answered "what is stored", and none covered enforcement, REM, the gate audit, intents or the journal — every subsystem shipped since v2.4. 5,418 lines of HTML replaced by ~1,900 lines of buildless ES modules in `src/mgcp/static/app/`.
- **Eight analytics endpoints** in `web_server.py`: `/api/signal`, `/api/retrieval/timeseries`, `/api/retrieval/misses`, `/api/effectiveness`, `/api/gate-audit`, `/api/enforcement/rules`, `/api/rem/state`, `/api/soliloquies`. They join `telemetry.db`, `lessons.db` and `gate_audit.jsonl` — the three stores the dashboard needs together and no existing module owns jointly. Named `/api/effectiveness` rather than `/api/lessons/effectiveness` because `/api/lessons/{lesson_id}` is registered earlier and captured it, returning `null` for a lesson called "effectiveness".
- **Eight views**: Signal, Retrieval, Effectiveness, Enforcement, Graph, REM, Journal, Curate. Curate keeps the editing the old pages had — lesson edit/delete and intent→skill compilation — against the same endpoints, so replacing the pages did not cost the CRUD.
- **The matched/bridged split is enforced everywhere.** A slot logged with score `0.0` was appended by the community bridge, not matched by relevance; `mean_matched_score` excludes them. Averaging the two is what made `mgcp-save-on-shutdown` read as 0.023 average relevance while scoring 0.510 whenever it was genuinely matched.
- **Chosen thresholds are labelled as chosen.** BGE cosine scores cluster high (median 0.691, max 0.791), so the retrieval-miss line is a slider that says so rather than a fact presented as one.
- **The palette was validated, not eyeballed.** Categorical slots were run through the data-viz validator against this surface: 4 adjacent slots pass every gate (worst adjacent CVD ΔE 8.4) and the first three pass all-pairs (ΔE 9.4), which is what the scatter uses. Status colours are never a series and never carry meaning alone — `good` vs `critical` measures CVD ΔE 4.1, the classic red/green confusion — so every status shows a glyph and a word.
- Verified by rendering: all eight views screenshotted at 390/768/1440px with zero console errors and zero horizontal overflow. Four real layout defects were found and fixed that way — a grid `minmax(420px)` floor wider than a phone, a `<select>` with no `max-width`, a flex nav without `min-width: 0` so its `overflow-x: auto` never engaged, and `.entry .body` unable to break the absolute paths quoted in gate-audit records.
- Corrected two things the UI itself exposed: `/api/graph` returns a **mixed** graph (1 root + 236 categories + 84 lessons + 9 workflows + 48 steps) with 23 duplicate ids, so calling all 378 nodes "lessons" was wrong; and only **77 of 297 lessons carry any edge at all**, which the Graph view now states.
- `docs/screenshots/`: the four dashboard images replaced with the new views. Enforcement and Journal are deliberately not pictured — they quote real session transcripts, including absolute paths from other projects.

### Added (v3.0 workstream A — Qdrant server mode)
Embedded remains the default: a single-session user should never have to run a daemon. Setting `MGCP_QDRANT_URL` opts in.
- `get_qdrant_url()` and `qdrant_client_args()` in `qdrant_vector_store.py` feed **all three** construction sites (both stores and the server's shared client), so the two modes cannot disagree about which store a process is talking to — a session silently writing to the other store is the failure that centralising this prevents. `MGCP_QDRANT_API_KEY` is passed only when non-blank, and a whitespace-only URL is treated as unset rather than producing a broken client.
- In server mode the stores no longer create a local directory, since the data lives wherever the server keeps it.
- `/api/health` now reports `qdrant_mode` and `qdrant_target`, because once two modes exist, which one is live is not otherwise visible.
- Both store docstrings already described server mode (`QdrantClient(host=..., port=6333)`) while the code only ever called `QdrantClient(path=...)`. The code now matches what was documented.
- **Verified end-to-end against Qdrant 1.19.1**, and the verification immediately found a showstopper. `_ensure_collection` is check-then-act: it lists collections, then creates if absent. Safe embedded, where there is only ever one client. In server mode two sessions starting together both saw "not exists", both POSTed a create, and the loser died on startup with `409 Conflict: Collection already exists` — so the second session was unusable, which is the entire audience for server mode. Another writer winning that race is success, not failure: the collection we wanted now exists. Both stores now tolerate it and anything else still raises. Afterwards, two concurrent MGCP processes each wrote three lessons and each read all six, with a third process confirming the union. Regression test `TestQdrantServerModeConcurrency` opens one new collection from four threads; it reproduces the 409 against the unguarded code and skips cleanly when no server is configured.
- **Correction to an earlier estimate.** The v3 design doc guessed a Qdrant daemon would cost "tens of MB" for a 6.4 MB corpus. Measured: **474.9 MiB** resident, idle, for 656K of stored data — comparable to one MGCP process (BGE is +403 MB), not noise. Server mode buys correctness, and it costs roughly another process's worth of memory. `mgcp-migrate` already rebuilds the index from SQLite, which is the embedded→server migration, and SQLite remains the source of truth, so the move is reversible.

### Added (v3.0 workstream C — concurrent writes fail loudly instead of silently)
First of the three v3 workstreams, deliberately ahead of Qdrant server mode: shipping concurrency before write-safety would turn a rare silent loss into a common one. Design in `docs/v3-multi-session-design.html`.
- **Compare-and-swap on lesson edits.** `lessons.version` was SELECTed but never used as a guard, so two sessions refining one lesson produced a lost update with nothing recorded anywhere. `update_lesson` now takes `expected_version`; the UPDATE carries `AND version = ?` and a zero rowcount raises `StaleWriteError` naming the expected and actual versions. `refine_lesson` returns a message telling the agent to re-read and re-apply; the web editor returns 409. Additive callers (`link_lessons`, `bootstrap`) pass nothing and are unguarded by design — a concurrent edit does not invalidate adding an edge.
- **Narrow project writes.** The real clobber was never concurrency, it was granularity: `save_project_context` replaces `todos`, `catalogue`, `notes`, `active_files` and `recent_decisions` wholesale from whatever the caller read, so a session that only meant to change the notes discarded another session's todo. `upsert_todo`, `append_decision`, `set_project_notes` and `set_active_files` read and write a single column inside one transaction, which removes the race rather than detecting it — SQLite serialises the writers, so no token and no retry. `add_project_todo` uses the narrow path, falling back to a full insert only when the project has no row yet.
- **`project_contexts.revision`**, added by an idempotent migration beside the others, bumped by every content write. Whole-context save remains last-writer-wins, which is acceptable only because it is now the exception (session close) rather than the only tool — and that behaviour is pinned by a test so it cannot drift unnoticed.
- **Atomic config writes.** `enforcement_rules.json` and `intent_config.json` were written with `open(p, "w")`, which truncates first: a crash or a concurrent reader saw a half-written or empty file, on exactly the files the PreToolUse hook reads on every tool call. Both now write a temp file in the same directory and `os.replace`.
- Tests cover the refusal, the surviving write, the missing-row case, unguarded writes, todo/notes interleaving and decision dedup — and were verified to fail against the unguarded code rather than merely passing against the new.

### Fixed (v3.0 — activating a workflow no longer reports it complete)
`update_workflow_state` cleared `workflow_complete` only when the workflow id *changed*, so re-running a finished workflow reported `Status: COMPLETE` from its first step. The hook reads that flag to decide whether to keep injecting workflow context. Activation now always clears it, while an explicit `workflow_complete=True` in the same call still wins.

### Fixed (v2.13, second pass — the remaining 89 findings, applied in disjoint batches)
Eight agents, one per non-overlapping file group, each re-verifying its findings against the post-v2.13 tree before touching anything. 89 applied, 19 rejected on re-verification (stale, already fixed, or the fix would have broken something). Grouped by what they actually were:

**Enforcement was bypassable four different ways.** Every one of these let a gated tool call through:
- `git` detection missed an absolute path (`/usr/bin/git commit`), a wrapper (`sudo git commit`, `env git commit`) and a `VAR=value git commit` prefix — all three are ordinary shapes, and all three walked past `git-requires-query-lessons`.
- A **string** in `turn_tools_called` or `turn_bypass_scopes` (rather than a list) made `in` match substrings, so one malformed state write opened every gate. The state file is agent-writable, which is why the isinstance check is load-bearing.
- `"command_match": {}` — an empty matcher — matched *every* call to the triggering tool instead of none.
- `MGCP_BYPASS:GIT` did not bypass: the scope was compared case-sensitively against a lowercase `bypass_scope`. Uppercase read as "no such scope", silently.
- An unknown precondition `type` silently disabled the whole rule; it is now recorded and the rule still denies.

**Data integrity.**
- `get_ancestors` **span forever on a parent cycle** — an `mgcp-import` can create one — and `get_statistics` walks every node's ancestry, so one cycle hung the whole call.
- Row parsers swallowed malformed JSON into an empty `relationships` list / empty `catalogue`, silently returning a lesson or project with its edges erased rather than reporting corruption.
- `save_workflow` and `save_community_summary` did not run the tool-call-envelope guard that the other four agent-facing writes run.
- `remove_catalogue_item` deleted custom items of *every* type that shared a title.
- `search_catalogue` silently searched every project when `project_path` could not be resolved.
- Three hand-rolled project-context creations used three different `session_count` seeds; unified, with sessions counted once by the tool whose comment already claimed to own the count.

**REM reported success it had not earned.**
- An unknown or misspelled operation name reported a clean run *and* wrote a junk `rem_state` row, which the SessionStart detector then read back as an overdue operation by that bogus name.
- `community_detection`'s orphan-lesson finding could never fire (Louvain partitions every node, so the membership set was always complete). It fires now, which means the first run on an existing corpus will list real singletons for the first time.
- `_knowledge_extraction` skipped whole projects based on a notes list it never read.
- `context_summary` was a registered operation that summarised nothing and had no writeback path — removed, taking `DEFAULT_SCHEDULES` to 6.
- `_gate_audit_review` claimed a windowing it did not do and did not name the hook fail-opens it counted.

**Retrieval.** `telemetry` recorded community-bridged lessons at score `0.0`, which corrupted `avg_score` for every lesson the bridge ever touched — the same defect as the bridge fix above, on the reading side.

**Installer and CLI.**
- `get_mcp_server_config()` wrote a layout-derived `cwd` into every client config, pinning the server's working directory to MGCP's own source tree.
- `init_global_hooks` seeded `enforcement_rules.json` somewhere other than where the hook and the MCP tools read it.
- `mgcp-import` on a *projects* export reported "Total: 0" instead of an error; `mgcp-export all` without `-o` printed two JSON documents to stdout; `import_lessons` died on an unbound name when an entry was malformed instead of reporting it.
- `mgcp --help` omitted `mgcp-backup` and `mgcp-migrate`; export headers and `-V` now read `mgcp.__version__` rather than stale literals.
- `migration.py` stopped describing itself as a ChromaDB migrator — it rebuilds the Qdrant index from SQLite, and ChromaDB has not been a dependency for two releases.

**Efficiency.** The catalogue store paged through every point in the collection to count a project and again to delete one, where Qdrant does both server-side with a filter.

**Deleted, each after a repo-wide sweep:** three `@mcp.resource` endpoints registered, documented nowhere and referenced by nothing; `render_full_routing` / `render_actions` and the two cache keys no hook has read since v2.5; `Soliloquy.to_context()`; seven `ProjectCatalogue` fields with no writer and no reader; `get_community_for_lesson` and the tests-only `get_by_relationship_type` wrapper; `idx_workflow_tags` (an index on a JSON column no SQL filters on); `HOOK_SCRIPT` and the eight tests that grepped its source text; `_owns_client` in both Qdrant stores; `EventType.BOOTSTRAP`; two unreachable `repair_rem_state` guards; and both stores' ChromaDB interface-parity promises.

**Tests.** Removed a further batch that could not fail — three failure-recovery tests with no assertion or an assertion inside a swallowing `try`; three legacy-hook absence tests pointing at a directory the v1 hooks never occupied; a hand-written "templates exist" list naming 4 of 5; a retrieval-floor test that was its own strict-xfail's complement. **`test_cli_specific_client` was deleted for triggering an unbounded 415 MB model download to assert something that could not fail.** Five per-client tests became one enumeration over all eight. `conftest`'s ledger check no longer drops skipped claim tests, and no longer reports an in-body skip as a failure.

### Fixed (v2.13 — found while writing the metrics views, not by the review)
- **`get_ancestors`, `configure_client` and two can't-fail tests** are above. Separately, tightening a test that asserted `status in [all four possible values]` exposed that `configure_client` **raised `PermissionError`** on an unwritable config directory instead of reporting it — and `mgcp-init` configures eight clients in a loop, so one bad directory aborted the other seven. Three unguarded write sites (an eager `mkdir` and two identical `write_text` calls) collapsed into one guarded write returning the `status: "error"` the function's own vocabulary already had.
- **`TestSaveCommunityAndSearch::test_save_and_search` had never executed an assertion.** It parsed community ids with `` `(comm_\w+)` ``, but `community_id` is a bare 12-character sha256 prefix (`graph.py`), so the pattern never matched, `if comm_ids:` never entered, and the whole body was skipped from the day it was written.

### Changed (v2.13 — documentation rebuilt rather than patched)
- **`docs/mgcp-interception-flow.html` regenerated from the code.** The previous version described v2.4: 553 lines and 8 diagrams with zero mentions of `apology`, `adjudicate`, `gate_audit`, `turn_session_id` or `rem_state` — nine feature-versions stale on a document whose entire subject is enforcement, while CLAUDE.md pointed readers at it for "the full interception map". Patching it would have cost more than rebuilding. The new version covers the five hooks, the single enforcing decision path, per-turn state ownership, the four audit event kinds, both retrieval paths, REM's per-project cadence, the store map, and four honestly-labelled remaining gaps.
- **CLAUDE.md's hook table restated instead of appended to.** Rows had become changelog-in-a-table (`v2.5: … v2.6: … v2.7: … v2.11: …`), which is why they kept going stale — each release appended a clause rather than describing the present. They now describe current behaviour; history stays here.
- **`docs/CAPABILITIES.md`: 30 line-number anchors removed.** They had rotted for the third time in one day; every anchor was already followed by the quoted claim that identifies its target, so the numbers carried no information and guaranteed future drift. Row **E05** moved CLAIMED → VERIFIED: it had asked for "a measurement of whether bridging surfaces useful neighbours" and predicted the `0.0` score — that measurement now exists and the defect it predicted is fixed. Scoreboard updated in the same commit, as the ledger's own rule requires (its self-check caught the omission).
- **`examples/mcp-config.json`** set `MGCP_DB_PATH`, which **no code reads** — anyone copying the example got a silently ignored setting. It now sets `MGCP_DATA_DIR`, which is the variable `get_default_db_path()` actually honours, and drops the `cwd` the installer no longer writes.
- `rem_report`'s docstring and both doc tables stopped promising "the last cycle's findings": only `{"finding_count": N}` is persisted, so it can show schedule state and counts and nothing more.
- CLAUDE.md's PreToolUse row carried two audit sentences, the first overclaiming that *every* decision is recorded. The log records `deny`, `comply`, `adjudication` and `human_bypass` — friction, not traffic — and allowed calls leave no entry.
- README's hook table credited UserPromptSubmit with "full classifier+actions re-injection"; it injects the terse block, and the full renderings were deleted as dead.
- Removed pyproject's `"scripts/*" = ["E501"]` per-file ignore: `.gitignore` re-includes exactly one script, and that one is clean, so the ignore governed no tracked file.

### Removed (v2.13 — copies with no caller)
Each confirmed by a repo-wide sweep including tests, scripts, examples, docs, `static/*.html`, yaml and `pyproject.toml`.
- **`enforcement.py`'s evaluator half** (573 → 260 lines). `server.py`/`init_project.py` import only the Pydantic schema and `default_config`/`load_config`/`save_config`; `evaluate_rules`, `trigger_matches`, `evaluate_precondition`, `check_coupling`, `get_staged_files`, `tokenize_command`, `detect_git_subcommand`, `parse_bypass_scopes` and `Violation` were imported *only* by `tests/test_enforcement.py`. The documented "shared behavioral contract" was two suites over two implementations, one of which never decided a tool call. The evaluator now lives in the hook alone; its unit tests were retargeted at the hook module via the existing `hook_module` fixture. That move exposed a real gap: the production `MGCP_BYPASS` regex in `user-prompt-dispatcher.py` had **no** test — every other bypass test injects `turn_bypass_scopes` directly — because the only tests of that logic exercised the dead copy. Six tests added against the live parse.
- **`examples/claude-hooks/{session-init,user-prompt-dispatcher,pre-tool-dispatcher,post-tool-dispatcher,mgcp-precompact}.py`** (1,314 lines): byte-identical to `src/mgcp/hook_templates/`, with no reader, no test, no installer path and no sync mechanism — and it had already drifted 3.5 months out of date once. `examples/claude-hooks/settings.json` and `legacy/` are kept (README and `tests/test_routing_integration.py` reference them). `examples/README.md` now points at the packaged templates, and its manual-setup line no longer points at `.claude/hooks/`, a directory that does not exist.
- **`llm-memory-mcp-design.md`** (516 lines): pre-v2.2 design doc contradicting the shipped system; its CLI table duplicated CLAUDE.md's and its "Next Steps" duplicated project todos [5]/[6].
- **`check_install.py`** (288 lines): no caller, no documentation, and a numpy pin contradicting `pyproject.toml`.
- **`data_ops.suggest_tags`** (45 lines) and the four tests that were its only consumers (94 lines).
- **Two dead tables in `lessons.db`**: `telemetry_events` and `lesson_usage` duplicated `telemetry.db`'s schema and held 0 rows after 102 sessions. `telemetry.py` uses `events`/`sessions`/`lesson_stats`.
- **`tests/test_smoke.py`** (321 lines): subsumed by `test_basic.py` (models/store/graph/vector), `test_integration.py` (end-to-end) and `test_claims.py::C05`, which resolves *every* documented entry point from README + pyproject rather than four hardcoded `--help` calls. Its one unique assertion — `len(LLM_CLIENTS) >= 8` — moved into `test_init_project.py::test_all_clients_registered`, strengthened from 5 spot-checked names to an exact set of all 8 (closing the gap that `zed`, `claude-desktop` and `cody` were untested).
- **`TestMemoryUsage`** in `test_stress.py` (57 lines): `ru_maxrss` misused twice — it is *peak* RSS, so `after - baseline` can only be ≥0 and is normally 0, and on macOS it is already in bytes so `* 1024` inflated it 1024×. It failed locally ("1000 lessons used 1308.6MB") while `pytestmark = pytest.mark.slow` and CI's `-m "not slow"` meant it never ran remotely. `test_stress.py` now passes completely.
- **`TestGapDetection`** in `test_trigger_coverage.py` (84 lines): both tests build a report, print it, and assert nothing; the one real assertion was commented out.

### Changed (v2.13)
- **`hook_templates/VERSION`**: 2.12 → 2.13. Load-bearing, not cosmetic: `init_project` auto-upgrades installed hooks only when the on-disk marker differs from the template version, so without the bump the stderr and `turn_session_id` fixes would never reach anyone's `~/.mgcp/hooks/`.
- **CLAUDE.md**: drops `migrations.py` from Core Components (deleted in v2.12 — by the same release that claimed to have repaired that list); the "Schema + evaluator + defaults live in enforcement.py … both sides are tested against the same behavioral contract" paragraph rewritten to describe one evaluator; the config-load asymmetry paragraph rewritten for fail-loud; status line corrected to v2.13 (it has now twice been written stale in the same commit that bumped the file).
- **`docs/mgcp-interception-flow.html`**: removes the `reminder_state.json` node and its incorrect `UP -->` edge (the dispatcher writes `workflow_state.json`), and relabels the telemetry store `telemetry.db` — `mgcp.db` is a 0-byte orphan no code references.
- **`qdrant_catalogue_store.py`**: imports `DEFAULT_QDRANT_PATH`/`string_to_uuid` from `qdrant_vector_store` instead of re-declaring them with an identical namespace UUID. Two definitions of that namespace is two chances for point ids to diverge.

### Rejected on review (v2.13)
- A reader proposed cutting ~627 of the 682 lines of `tests/test_intent_benchmark.py` as "asserting nothing or testing only its own constants", and both adversarial verifiers upheld it. Both were wrong, and the check they skipped was README:307. `classify_regex` and `GraphCommunityClassifier` are defined in that file because they are the two baselines behind the published measurement "LLM self-routing improved accuracy by ~50% over regex … graph-community classification was not competitive". Deleting them would have orphaned a documented result in a project that maintains a CAPABILITIES ledger to keep claims measured. Recorded here because a verified-then-rejected finding is the interesting kind.

### Removed (v2.12 — dead-code excision, adversarially verified)
Every deletion below was confirmed by a measured usage sweep (grep across src/tests/examples/scripts/docs/yaml/html/pyproject, dynamic-dispatch paths included) by a 12-agent review; zero callers outside the definitions and, where noted, their own tests.
- **`src/mgcp/migrations.py` + `tests/test_migrations.py`** (~1,035 lines): orphaned one-time migration runner — no console-script entry, no docs, no runtime caller; `persistence.py` independently creates the same tables and runs the live, idempotent versions of its still-relevant repairs (`_run_migrations`, `repair_rem_state`) on every store open. Keeping both meant maintaining repair logic twice (they had already drifted on `~`-expansion).
- **`graph.py`**: `get_relationships`, `get_next_in_sequence`, `get_descendants`, `find_path` (zero callers); `get_prerequisites`, `get_alternatives` (tests-only wrappers — the two test call sites now use `get_by_relationship_type` directly).
- **`models.py`**: never-instantiated Pydantic models `LessonSummary`, `QueryResult`, `LessonVersion`, `ContextSnapshot` (versioning/context-history flow through raw SQL + dicts end to end).
- **`persistence.py`**: `get_lessons_by_tags` (zero callers; also raised OperationalError on an empty tag list), `delete_workflow` (no tool/route/test; a real delete would also need the Qdrant counterpart that doesn't exist), `delete_community_summary` (tests-only).
- **`qdrant_vector_store.py`**: `search_similar`, `remove_community_summary` (tests-only).
- **`telemetry.py`**: `end_session` + `EventType.SESSION_END` (never called; every session row was opened and never closed — the `ended_at` column stays for old rows).
- **`logging_config.py`**: `set_log_level`, `cleanup_old_logs` (zero callers).
- **`reminder_state.py`**: `check_and_consume_reminder`, `increment_counter`, `get_workflow_state` (the live dispatcher inlines this logic; only the archived legacy hook used its own local copies).
- **Web UI Phase 8 ghosts**: `lessons.html` graduation UI (stat tile, amber badges, dimming, detail field — keyed on a `graduated_to` field no API returns and the Lesson model silently drops, so it could never render) and `architecture.html` `CompiledSkill` class / `graduated_to` field & edge / "lessons graduated" sequence step (README architecture screenshots regenerated).
- **`scripts/seed_demo_skills.py`** (local, gitignored): died unconditionally at import on the removed `CompiledSkill` model, breaking `capture_screenshots.py --demo`; its subprocess invocation removed too.

### Fixed (v2.12 — review findings, each confirmed with a concrete failure trace)
- **Test suite hung at exit** (local dogfood machines): `LessonStore`'s connection pool never closed its pooled aiosqlite connections, and each one owns a non-daemon worker thread that blocks interpreter shutdown when the store stays referenced (e.g. `server._store`). Pooled connections are now stopped via `threading._register_atexit` (the pre-thread-join hook `concurrent.futures` uses). The suite (902 tests, ~60s) previously printed its summary and then hung forever.
- **Apology gate self-cleared after any tool call**: the transcript walk in `pre-tool-dispatcher.py` stopped at the first `type=="user"` entry, but tool results are recorded as `type=="user"` entries — so one tool call (even a denied or discovery-exempt one) hid the apology and reopened the gate. The walk now skips tool_result-only user entries and stops only at genuine prompts. Regression tests added; both hook copies synced.
- **`examples/claude-hooks/settings.json` omitted PreToolUse entirely** — a manual copy-install got zero enforcement while all docs describe the enforcing gate. (The file predated the enforcement dispatcher.) Entry added, matching `init_project._build_hook_settings()`.
- **`mgcp-backup --restore` tar-slip**: `shutil.unpack_archive` extracted user-supplied archives with no member filtering (closes the open MEDIUM security note). Now uses `tarfile` with `filter='data'` plus an explicit member-path check, and handles archives whose root dir name differs from the restore target (previously restored to the wrong directory while printing success).
- **REM duplicate findings always rendered `? / ?`**: `_duplicate_detection` read flat `lesson_a`/`trigger_a` keys that `find_duplicates` never emits (it returns nested `lesson_1`/`lesson_2` dicts). Reads fixed; metadata keys kept (session-detail.html depends on them); regression test added.
- **`remove_catalogue_item` left stale vectors in Qdrant** for error patterns, couplings, and custom items: the vector delete rebuilt the doc ID from the raw identifier while add-time IDs derive from `signature[:30]` / `files[0]` / `item_type+title`, and only one delete was issued for multi-row removals. Vector deletes now use the add-time derivation, once per removed object.
- **`get_catalogue_item` couldn't fetch custom items** (`Unknown item type`) even though add/search/remove all support them — the documented lifecycle broke at `get`. Custom fallback added, mirroring remove's `type:title` handling.
- **`link_lessons` silently no-opped on same-target/different-type links** and still returned success: dedup was on target only, so a `contradicts` link after a `related` link claimed success while storing nothing. Dedup is now on (target, type) and a pure no-op returns "already linked".
- **`delete_lesson` orphaned references**: children kept a dangling `parent_id` (unbrowsable — `list_categories` lost them) and inbound relationships resurrected the deleted ID as a ghost graph node on next startup. Deletion now nulls children's `parent_id` and strips inbound relationship entries in the same transaction.
- **`_ensure_initialized` failure bricked the server**: a mid-init exception leaked the local-mode Qdrant client (which holds the storage lock) and left partial globals, so every retry failed with "Storage folder already accessed" until process restart. The error path now closes the client and resets globals.
- **`import_lessons --merge overwrite` orphaned Qdrant vectors** on trigger-duplicates with a different id (semantic search returned lessons `get_lesson` couldn't find), and **export→import silently reset `created_at`/`usage_count`/`last_used`** (suppressing REM staleness findings for 30 days and killing the heavily-used-but-stale branch). Both fixed.
- **`mgcp --version` printed 2.0.0** while everything else says 2.1.0; now reads `mgcp.__version__`.
- **`update_project_todo` accepted invalid statuses silently** and reported the todo "updated to" its unchanged status; now returns an error listing valid values.
- **Dormant validators wired in**: `_validate_lesson_id` / `_validate_relationship_type` existed but were never called — `add_lesson` accepted malformed IDs and `link_lessons` surfaced raw Pydantic errors instead of the curated messages. Both now run first.
- **`MGCP_ENFORCEMENT_CONFIG`** was honored by the PreToolUse hook but not by `enforcement._config_path`, so with it set the MCP tools edited a file the hook never read. The library now resolves it identically.
- **Active todos now display their real index** (`[7]`) in `get_project_context` — `update_project_todo` indexes the full list including completed entries, so positions within the filtered view sent updates to the wrong todo.

### Changed (v2.12 — documentation accuracy)
- **CLAUDE.md**: status line now names both version lines (package 2.1.0 vs hook/feature v2.11) instead of the contradictory "v2.1.0 … ships (v2.3)"; Core Components list gains the three missing live modules (`enforcement.py`, `intent_config.py`, `skill_compiler.py`); session-init hook row catches up to v2.6/v2.7 detectors; the hook-vs-library config-load asymmetry (hook fails open to no rules, tools fall back to defaults) is now documented.
- **examples/README.md**: hook table rewritten from the v1 set (two hooks now in `legacy/`, two `.sh` files that never shipped) to the five shipped dispatchers, noting which one enforces.

### Added (v2.7 — REM enforcement gate + visibility)
- **`src/mgcp/enforcement.py` `DEFAULT_RULES`**: two new rules seeded on fresh installs.
  - `rem-required-before-commit` (**enabled=False by default**): blocks `git commit` / `git push` unless `mcp__mgcp__rem_run` was called in the same turn. Default-off because fresh installs without lesson history do not benefit from REM enforcement; toggle on via `mcp__mgcp__toggle_enforcement_rule('rem-required-before-commit')` once REM has run a few cycles. Bypass scope `rem`.
  - `version-bump-requires-readme` (**enabled=True**): blocks commits/pushes that stage `src/mgcp/hook_templates/VERSION` without also staging `README.md`. Backports the rule that was added out-of-band via `add_enforcement_rule` after v2.4 shipped, finally seeding it for new installs as the recent-decisions log called for. Bypass scope `docs`.
- **`hook_templates/session-init.py` REM-overdue detector**: new `_find_overdue_rem_operations()` reads `~/.mgcp/lessons.db` (stdlib `sqlite3`, read-only URI mode, 2s timeout) and compares `rem_state.next_due_session` against `project_contexts.session_count` for the current project. Injects a `## ⚠️ REM Operations Overdue` block listing each overdue operation with its gap (sessions overdue) and the recommended action (`rem_run` plus the optional toggle for `rem-required-before-commit`). Fail-open on missing DB, missing tables, missing project row, or any sqlite error. Mirrors the v2.6 stale-hook-reference detector pattern.
- **`tests/test_session_init.py`**: 7 new REM-detector tests covering DB-absent fail-open, no-overdue clean path, single-overdue warning text, multiple-overdue listing (verifying non-overdue rows are NOT mentioned), unknown-project fail-open, malformed-DB fail-open, and the toggle hint in the action footer.

### Changed (v2.7)
- **`hook_templates/VERSION`**: 2.6 → 2.7. Installer auto-upgrades existing installs on next session.
- **`hook_templates/session-init.py`** + **`examples/claude-hooks/session-init.py`**: kept in sync; both now carry the REM detector and the v2.7 docstring.

### Removed (v2.7 follow-up — bootstrap data)
- **`bootstrap_data/dev/git-practices.yaml`**: dropped the `pre-commit-documentation-review` lesson. Its function is now mechanically enforced by the `version-bump-requires-readme` rule (for hook_templates/VERSION → README parity) plus the broader `add_enforcement_rule` machinery for any other doc-coupling the user wants to gate. Keeping it as advisory wisdom alongside enforcement created a redundant warning that the LLM would skim past.
- **`bootstrap_data/dev/relationships.yaml`**: dropped the two graph relationships that pointed at or from the removed lesson (`git-practices → pre-commit-documentation-review` prerequisite and `pre-commit-documentation-review → verify-before-push` complement).
- **`bootstrap_data/dev/workflows.yaml`**: dropped the `pre-commit-documentation-review` step lesson from the `qa-release` workflow.
- **`bootstrap_data/core/relationships.yaml`** **does NOT** reference `version-consistency-across-docs` (verified by grep) — that lesson, also deleted from this dogfood install, was apparently never seeded into bootstrap, only added ad hoc in this project's history.

### Notes (v2.7)
- **Existing installs do not auto-migrate the new DEFAULT_RULES.** The seed in `init_project.py:720` writes `~/.mgcp/enforcement_rules.json` only when the file does not already exist (preserves user edits). To pick up the new rules on an existing install, either delete the file (loses user edits) or call `mcp__mgcp__add_enforcement_rule` for each. The SessionStart REM-overdue detector activates immediately on upgrade because it reads the DB directly, not via the rules file.
- **`rem-required-before-commit` is default-off on purpose.** Default-on would force `rem_run` on every commit even on fresh installs without REM state, producing no-finding noise that trains the LLM to skim past REM output. Default-off plus the SessionStart visibility layer surfaces overdue state at the right moment (session start, not commit time) and lets the user opt into commit-time enforcement once REM is producing useful findings.
- **The session-97 dogfood incident drove this release.** REM had not run on this project for 13 sessions (about 2 months) because there was no auto-trigger and no visibility surface. The advisory channel had failed silently. The combination of seeded rule plus SessionStart detector closes both halves: the rule provides commit-time enforcement (when opted in) and the detector provides every-session visibility (always).

### Fixed (v2.6 — stale hook reference cleanup)
- **`src/mgcp/init_project.py`**: the legacy-command scrub in `settings.json` was gated behind `force=True` in both `init_claude_hooks` and `init_global_hooks`. `mgcp-init` (no flags) would delete a legacy hook file from `~/.mgcp/hooks/` but leave the reference in `~/.claude/settings.json`, producing `hook returned blocking error` / `Errno 2: No such file or directory` noise on every matching tool call. The scrub is now unconditional — file removal and reference removal are two halves of the same cleanup. Extracted to `_scrub_legacy_hook_commands(existing)` helper. Only compacts groups/types it actually modified; pre-existing empty structures are preserved.
- **`hook_templates/session-init.py`**: new session-start check — scans `~/.claude/settings.json` AND `$CLAUDE_PROJECT_DIR/.claude/settings.json` for hook commands referencing missing absolute `.py` paths and injects a `## ⚠️ Stale Hook References Detected` block with a "run `mgcp-init --force`" fix instruction. Stdlib-only, fails open on parse errors, one `stat()` per hook command.
- **`hook_templates/VERSION`**: 2.5 → 2.6. Installer auto-upgrades existing installs on next session.
- **`tests/test_init_project.py`**: new `TestScrubLegacyHookCommands` class + regression tests `test_upgrade_without_force_scrubs_legacy_from_settings` (project) and `test_global_upgrade_without_force_scrubs_legacy_from_settings` (global). Guards the exact failure mode reported (`mgcp-reminder.py` orphan reference).
- **`tests/test_session_init.py`**: new test module driving `session-init.py` as a subprocess. Covers: no warning when settings absent, no warning when scripts exist, warning for global settings orphan, warning for project settings orphan, malformed JSON fails open, relative paths ignored.

### Notes (v2.6)
- **If you're upgrading from v2.0/v2.1 to v2.6+**: run `mgcp-init --force` once. Fresh installs and re-runs of `mgcp-init` now scrub automatically, but existing broken machines don't self-heal on upgrade — they get the advisory warning at session start, telling the LLM to instruct the user.
- The PostToolUse "blocking error" UI wording is a Claude Code labeling quirk, not a MGCP bug. Any non-zero PostToolUse exit gets labeled that way even though PostToolUse cannot actually block a tool call. The Write/Edit always succeeded.

### Changed (v2.5 — SessionStart dedup)
- **`hook_templates/session-init.py`**: no longer injects `<intent-routing>` or `<intent-actions>` blocks. The UserPromptSubmit dispatcher already re-renders the full classifier+inline-actions block from `rendered.dispatcher_routing` on every message, so the SessionStart copy was pure duplication. SessionStart injection drops from ~2500 → ~1050 chars (~300 tokens saved per session). SessionStart now carries only the bootstrap checklist (soliloquy / project context / query_lessons) and the workflow execution discipline.
- **`hook_templates/VERSION`**: 2.4 → 2.5. Installer auto-upgrades existing installs on next session.
- **`tests/test_routing_integration.py`**: `TestSessionInitOutput` updated — `test_contains_intent_routing_tags` / `test_contains_all_seven_intents` replaced by `test_no_intent_routing_tags` and `test_contains_session_start_checklist` asserting the new (leaner) contract.
- **`intent_config.py`** rendering is unchanged — `session_init_routing` / `session_init_actions` keys still populate the on-disk cache for backwards-compat consumers, just no longer read by the hook. A follow-up could drop them outright.

### Notes (v2.5)
- Walks back an earlier sketch of "hook-side intent classification" (regressing to legacy regex-keyword matching). Classification stays LLM-side; enforcement rules catch misclassification at tool-call time. The dedup is the scope of this release.

### Added (v2.4 — enforcement-as-data)
- **`src/mgcp/enforcement.py`**: New Pydantic-schema + load/save + evaluator module. Enforcement rules are now **data**, not hardcoded: each rule pairs a `Trigger` (which tool calls it matches) with `Preconditions` (what must hold for the call to proceed) and a `bypass_scope`. Rules live in `~/.mgcp/enforcement_rules.json` (override with `MGCP_DATA_DIR`). Matches the v2.2 routing-as-data philosophy — new rules are added from chat via MCP tools, with **no code changes**.
- **Precondition types**: `tool_called_this_turn`, `tool_not_called_this_turn`, and `staged_files_coupling` (blocks a commit until a glob-matched file is also staged, e.g. "if `src/**/*.py` is staged, `CHANGELOG.md` or `README.md` must be too"). The staged-file evaluator runs `git diff --cached --name-only` under the hood.
- **Scoped bypass**: `MGCP_BYPASS:<scope>` in the user prompt disables rules whose `bypass_scope` matches; bare `MGCP_BYPASS` disables all. Replaces v2.3's all-or-nothing `turn_bypass` flag. UserPromptSubmit parses every `MGCP_BYPASS[:<scope>]` token and writes `turn_bypass_scopes: list[str]` to `workflow_state.json`.
- **Generic `turn_tools_called: list[str]`** replaces v2.3's `turn_query_lessons_called: bool`. PostToolUse appends *every* tool name (not just `query_lessons`), so `tool_called_this_turn` preconditions can target arbitrary tools.
- **6 new MCP tools** for enforcement CRUD: `list_enforcement_rules`, `get_enforcement_rule`, `add_enforcement_rule`, `update_enforcement_rule`, `remove_enforcement_rule`, `toggle_enforcement_rule`. Edits take effect on the next tool call (the hook re-reads the JSON each time) without restarting Claude Code. Tool count: 43 → 49.
- **`tests/test_enforcement.py`**: 35 unit tests covering the schema, detectors, preconditions, rule evaluation, bypass parsing, and persistence round-trip.
- **Default rule seeded on install**: `init_project.py` writes `~/.mgcp/enforcement_rules.json` containing the `git-requires-query-lessons` rule on first hook install. Never overwrites user edits across upgrades.

### Changed (v2.4)
- **`pre-tool-dispatcher.py`**: rewritten as a **stdlib-only generic evaluator** reading `enforcement_rules.json`. Same semantics as `src/mgcp/enforcement.py` (shared behavioral contract exercised by both test modules). No `mgcp` import — the hook stays independent of the server.
- **`user-prompt-dispatcher.py`**: replaces `turn_query_lessons_called` / `turn_bypass` with `turn_tools_called=[]` and `turn_bypass_scopes=[...]`. Bypass parser is now regex-scoped, not a single boolean.
- **`post-tool-dispatcher.py`**: replaces `_mark_query_lessons_called()` with `_append_tool_called(tool_name)` so arbitrary tools can be tracked.
- **`hook_templates/VERSION`**: 2.3 → 2.4. Installer auto-upgrades.
- **Tests**: `test_pre_tool_dispatcher.py` rewritten around the generic evaluator — drives the hook with a temp `enforcement_rules.json` and tests scoped / star / unrelated bypass, disabled rules, and both fail-open paths (missing rules file vs. missing state file).

### Notes (v2.4)
- Continues the v2.2 → v2.3 arc: v2.2 made the routing prompt data; v2.3 introduced the first enforcing hook (hardcoded); v2.4 makes **enforcement itself data**. The LLM can now add new interrupts from chat — e.g. "whenever I stage a Python file in `src/`, require `CHANGELOG.md` to be staged too" is one `add_enforcement_rule` call.
- Fails open everywhere. Malformed JSON, unknown precondition types, subprocess errors on `git diff` — all allow the tool call. Enforcement is a safety net, not a tripwire.

### Fixed (v2.4 follow-ups)
- **Windows hook portability** (gap #7): installed hooks now invoke `sys.executable` via a new `_hook_python_command()` helper (shlex-quoted for paths with spaces) instead of the hardcoded `python3`. Portable across Windows (where the default is `python`), venvs, and custom interpreter paths.
- **Flowchart gap table** (`docs/mgcp-interception-flow.html`): updated to mark #7 resolved and #5/#9 as "mechanism ✓" — v2.4 shipped the infrastructure (`tool_called_this_turn` and `staged_files_coupling` preconditions) but maintainers opt in per-project because the rules are repo-specific.
- **New "Recipes" section** in the flowchart: three copy-pasteable `add_enforcement_rule` calls that close the remaining gaps as one-liners — doc/code parity, CHANGELOG discipline, Edit-requires-catalogue-capture.
- **Apology-gate in `pre-tool-dispatcher.py`**: promotes the MEMORY.md "apologies must trigger a knowledge write" rule from passive note to hard enforcement. Reads `transcript_path` from the hook input, extracts assistant text since the most recent user message, and denies any non-`add_lesson` tool call when apology markers (`sorry`, `my bad`, `you're right`, `my mistake`, `apolog(y|ies|ize|ise)`) are present. Hardcoded rather than rules-driven because the trigger is assistant text, not a tool argument. Bypass via `MGCP_BYPASS:apology`. 9 new tests cover detection, deny/allow paths, prior-turn scoping, and fail-open on missing transcripts.

### Added (v2.3 hook templates — PreToolUse enforcement)
- **`src/mgcp/hook_templates/pre-tool-dispatcher.py`**: First ENFORCING MGCP hook. Prior hooks (SessionStart, UserPromptSubmit, PostToolUse, PreCompact) are all advisory — they inject text as `<system-reminder>` tags that the LLM may skim or ignore. PreToolUse returns `permissionDecision: "deny"` and the tool call is refused by the Claude Code harness. First enforced rule: `git commit` / `git push` is blocked unless `mcp__mgcp__query_lessons` ran in the same turn. Bypass token `MGCP_BYPASS` in the user prompt disables enforcement for that turn.
- **Quote-aware command detection**: the detector uses `shlex.shlex(..., punctuation_chars=True)` + `whitespace_split=True` so quoted strings stay as single tokens and shell operators (`;`, `&&`, `||`, `|`, `()`) become their own tokens. `grep 'git commit' docs/` and `echo "how to git commit"` correctly pass through; `make build && git push` correctly blocks.
- **Per-turn state in `workflow_state.json`**: UserPromptSubmit resets `turn_query_lessons_called = False` every message and sets `turn_bypass` from the prompt. PostToolUse flips `turn_query_lessons_called = True` when the tool runs. PreToolUse reads both.
- **`docs/mgcp-interception-flow.html`**: Comprehensive mermaid.js flowchart of all 5 hook interception points (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact), the growth loop, data stores touched per session, and 10 known gaps with remediation ideas. Per project convention (HTML over ASCII art).
- **`tests/test_pre_tool_dispatcher.py`**: 14 tests covering detector true/false positives (including the quoted-string false-positive the naive regex hit during bring-up), subprocess-level deny/allow/bypass flows, and fail-open behavior on malformed state or input.

### Changed (v2.3 hook templates)
- **`hook_templates/VERSION`**: 2.2 → 2.3. The installer auto-upgrades installed hooks in `~/.mgcp/hooks/` when the on-disk marker differs from the template version (previously required `--force`, which was the root cause of the silent v2.0 → v2.2 drift).
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
