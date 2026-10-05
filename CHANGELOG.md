# Changelog

All notable changes to MGCP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added: structured coding gates, all shipped switched off
- **Four new precondition types turn code structure into something the PreToolUse hook checks, instead of something a prompt asks for.** A diff budget on every edit, a complexity ratchet on every commit, a required `Why:` paragraph, and a required catalogue decision when a commit adds a module. Design, decisions and measurements are in [docs/structured-coding-gates.md](docs/structured-coding-gates.md).
- **Every new rule ships disabled AND in audit mode.** An audit rule records what it would have refused and allows the call. Two switches rather than one, because `mode` is a key an older deployed hook ignores rather than rejecting, and ignoring it means enforcing. That is the state after a git pull and before `mgcp-init`.
- **A ratchet, not a limit.** 58 of 463 functions here are already over cyclomatic complexity 10, so a fixed limit would refuse every commit touching them. Changed code may not get worse on a metric it already fails, and new code must meet the limits.
- `hook_templates/quality_metrics.py` holds the measurement, stdlib only. The hook imports it and CI runs it as a script against a pull request's base, so one implementation serves both and a commit cannot pass locally and fail in CI for reasons nobody can reproduce.
- `sync_enforcement_rules` is a new MCP tool, and the only thing that delivers a shipped rule to an install that already has a rules file. It adds by name and only by name: a rule already present keeps every field and its position, because a populated rules file is a customised one and a shipped rule may differ from its default on purpose.

### Changed: the evaluator stopped growing a parameter per input
- **`_evaluate_precondition` went from 42 cyclomatic complexity to 8.** It was an if-chain over six types that gained a positional parameter for every lazily loaded input, and the four types added here would have made it ten parameters. Each type is now one handler behind a dict, and the git and transcript reads live on one lazy context object.
- The three ad-hoc git readers are gone with it, about 69 lines, replaced by accessors that cache per tool call. A rule that needs the staged diff costs one subprocess and a rule that needs nothing costs none.
- `Trigger.tool_names` lets one rule cover Edit, Write and MultiEdit instead of three copies that drift apart.
- `Trigger` and `Precondition` now set `extra="forbid"`. Every enforcement tool rewrites the whole file from these models, so a field the model does not know was silently dropped on the next write, turning a one-field typo into deleted enforcement.

### Changed: the hook payload version is a plain counter
- `hook_templates/VERSION` holds `18` and no longer looks like a release. It read `2.17` beside a package version of 3.0.0, which reads as the hooks being a major version behind, and that is how it was read. Its only job is to differ from the marker in `~/.mgcp/hooks/.mgcp-hook-version` so `mgcp-init` re-copies the hooks. Feature names such as v2.11 and v2.16 stay in the documents as historical labels.

### Added: the doctor reports hooks that a pull did not update
- **`mgcp-init --doctor` now reports a deployed hook payload behind the package, and any missing hook file.** A git pull does not refresh the installed hooks: `settings.json` names copies under `~/.mgcp/hooks` by absolute path, and only `mgcp-init` rewrites them. So new gate code and new rules can sit in the repository while the hook enforcing them is weeks old, and nothing said so.

### Fixed: three defects found by building the gate and running it on itself
- **The gate refused its own commit, 11 times.** Running the new ratchet against this change reported 11 violations in the code that adds it, including a dispatcher that had got worse rather than better. All of them are fixed, and the change now passes with none.
- **`fnmatch` cannot express `**`.** `src/**/*.py` did not match a module added directly in `src/`, which is the exact case `new-module-requires-decision` exists to catch. Both glob matchers handle the two `**` forms now.
- **The transcript rule refused when it could not read the transcript.** A missing `transcript_path` would have blocked every commit that adds a module. It allows and records a skipped row, which is what the hook's own fail-open invariant requires.

### Added: a history replay, so limits are calibrated and not guessed
- `tests/history_replay.py` runs each gate against the last 200 commits, comparing each to its first parent, and prints refusal counts plus the worst five per gate. Read only: every call is `git -C <repo> show` or `diff`, nothing is checked out.
- Measured on this repository: the complexity ratchet would refuse 36% of 200 commits, the diff budget 12%, and the `Why:` rule every one of the 81 eligible commits, because no past commit carries that paragraph. Added lines per commit: median 19, maximum 4,933.
- The ratchet's refusals read as real on inspection, so CC 10 stands as the starting limit rather than being relaxed to fit the code it judges.

### Added: the dashboard separates a projection from an action
- `/api/gate-audit` and `/api/enforcement/rules` count `would_deny` as its own total, its own daily series and its own per-rule tally, kept apart from real denials everywhere. A rule in audit mode that would have refused 40 commits must never be added to the same number as a rule that refused one.
- The Enforcement view shows each rule's mode, so a long `would_fire` column reads as a projection from a rule that is refusing nothing.

### Changed
- The `feature-development` workflow gains `fit` after `plan` and `simplify` after `test`. `bug-fix` gains `simplify`. Existing step IDs are unchanged, because stored workflow state resolves a step by ID.
- Six lessons added to `bootstrap_data/dev/code-quality.yaml`, one per banned pattern not already covered, and two existing lessons extended.
- `hook_templates/VERSION` moves to 18. Installed hooks upgrade on the next `mgcp-init` run, and the gates do nothing until then.


### Added: the doctor reports leftover server processes
- **`mgcp-init --doctor` now lists every running `mgcp.server` with its age and memory.** Reconnecting a client starts a new server and does not stop the old one, so they accumulate one per reconnect and nothing says so. One was found alive for 1 day 23 hours, holding a writable handle on `lessons.db` and 330 MB, most of that a second copy of the embedding model.
- On the default embedded vector store a leftover also holds the Qdrant directory lock, which permits one client per path. That is the 2026-10-01 failure where all 50 tools broke: a restarted session's old server still held the lock, the three calls the session-start hook requires failed one second apart, and the session ran with no memory and could not save any.
- The doctor reports and does not act. It cannot tell which server a client is attached to, so stopping one would risk disconnecting the user. It prints the pid and says to use `kill -TERM` rather than `-9`, because the process has an open database handle and needs to close SQLite cleanly.
- **The first version reported four servers when two were running.** `ps` prints a shell's whole command line, so a `sh -c` that merely mentions the module reads as another server. The filter now requires the command's executable to be a python interpreter. Found by starting a second server and comparing the count against `ps`, not by reading the code.
- Windows uses `wmic`, since `tasklist` does not show command lines. A platform where neither works reports that it could not check, rather than reporting none, because none would read as a clean result.
- 8 tests, including one that proves the shell filter bites.


### Removed: private identifiers from a public repository
- **Two of the author's other project names were in the tracked documents**, in CLAUDE.md, five rows of the claim ledger and one test docstring. One absolute home path was in CHANGELOG.md inside a quoted error message. All of it is gone, replaced by neutral descriptions that keep the measurement: "a second project at session 36" says the same thing as naming it.
- Each one arrived while citing real evidence. The REM scheduling bug needed another project's session count to be legible, and the Qdrant lock story needed the real error text. That is why a habit at writing time matters more than a sweep afterwards.
- Three sweeps were needed. The first was case-sensitive and found neither name as written. The second built its list from `list_projects` and found both. The third, after the replacements, found one occurrence the hand-written list had missed.

### Removed: 1.7 MB of row-level research output
- `docs/locomo-results/pq-*.json` (6 files, 1,616 KB) and the per-query row files in `docs/bridge-results` (7 files, 112 KB) are no longer committed. Nothing read them, they regenerate from the harnesses, and the bridge files held one operator's note identifiers.
- **The per-run totals stay**, about 80 KB across `cmp-*`, `abstention-*`, `paired-*`, `bridge-sweep` and `bridge-analysis`. Three claim tests compare the numbers in the write-ups against those files, so removing them would have turned eight VERIFIED rows into unverifiable ones for a further 4% of the size.
- Both patterns are in `.gitignore`, and the write-ups now say which files are kept, which are not, and the command that regenerates them.

### Added: two precondition types that keep things out of a public history
- `staged_files_forbid` takes `deny_globs` and refuses a staged path outright, asking for nothing in return, unlike `staged_files_coupling`.
- `staged_content_forbid` takes `patterns`, a list of regular expressions, and is the only type that reads the staged text rather than paths. That is what this needed: a private name arrives inside a sentence, so no amount of path matching sees it.
- **Both read only the lines a commit ADDS.** `git diff --cached --name-only` lists deletions, so a first version of the path rule refused the commit that REMOVED the forbidden file. Found while committing the removal above, where 11 deleted paths still read as staged. The content rule has the same hazard and solves it the same way.
- An unreadable diff, an invalid pattern and a directory that is not a repository are all skipped, because enforcement is a net rather than a tripwire. Each rule costs at most one `git` subprocess per tool call, read lazily and only when a matched rule asks.
- Two rules use them, `no-private-identifiers` and `no-research-row-output`. Their pattern lists live in `~/.mgcp/enforcement_rules.json`, which is not tracked, so a list of private names is never itself published. 10 tests.

### Added: two claim-ledger rows that check the documents
- **C34 checks that every repository path cited in prose exists.** Three rot events in two days were each a path in a document that no longer resolved, and each was caught by hand. Paths inside fenced commands are skipped, since a command may name output it produces. It caught its own ledger row on the first run, which cited the directory that had just been deleted.
- **C35 checks that no tracked file contains an absolute home path.** The name half of the problem cannot live in a test, because the list would be published by the test that hides it, so the names are the gate's job and the home paths are the test's.
- A new intent, `public_repo_hygiene`, carries the same guidance to the advisory hooks, and the lesson is `neutral-description-keeps-the-evidence`.

### Changed
- `hook_templates/VERSION` moves to 2.17. The two precondition types live in the hook, so they work after `mgcp-init` runs. The MCP tools that validate rules need the server restarted as well, because the schema is in the package.


### Fixed: the apology gate could be opened silently, and it read the wrong file
- **An adversarial audit of 154 agents found a silent bypass in the only hook that can refuse a tool call.** The gate bounded its transcript search by `turn_started_at`, read from `workflow_state.json`. Any value later than the model's own words stopped the search at once, so the gate found no text, allowed the call, and wrote no row to `gate_audit.jsonl`. That file is shared by every project and every concurrent session, and the model can write it, so another session's turn, a forged number such as 1e18 or infinity, and a clock correction all reached the bypass. Driving the hook with another session's value allowed a Bash call that should have been refused.
- The bound had been added one day earlier to fix a false refusal, and it left the mirror case open: an apology that had NOT been answered, written before a mid-turn message, escaped the same way.
- **Both directions are now settled from the transcript.** A call to `add_lesson` recorded later than the apology answers it, and nothing else does. Walking backwards, an `add_lesson` seen before the apology is one that happened after it, so a single pass decides whether an apology exists and whether it was paid for. Blocks are walked in reverse inside each message too, because one assistant turn can apologize in a text block and call `add_lesson` in a later block of the same message.
- `turn_tools_called` no longer answers the question either. Reading it is what produced the original false refusal, and it is as writable by the model as the timestamp was. The transcript is per session and the model cannot write it.
- Verified against the live transcript that produced the real false refusal: the sentence behind it was already answered, and the new code allows. Then verified in the other direction by reintroducing the old shortcut and watching exactly one test fail.
- `_entry_epoch`, the `datetime` import and the `_has_apology` wrapper are gone with the bound. 18 tests rewritten and 4 added, including one that puts every value an attacker could place in the shared file into it and requires a refusal anyway.

### Fixed: a malformed shared state file crashed two hooks
- **`workflow_state.json` holding `[]`, `42`, `"text"` or `null` crashed `user-prompt-dispatcher.py` and `post-tool-dispatcher.py` on every message and every tool call.** `pre-tool-dispatcher.py` has guarded that since v2.4, with a comment calling the check load-bearing, and the other two hooks never got it. Both parse the file and then assume a dictionary, neither has a top-level handler, and their `except` clauses do not catch the resulting `TypeError` or `AttributeError`.
- The effect was worse than lost advisory text. PreToolUse survived and kept refusing git without `query_lessons`, while the hook that parses `MGCP_BYPASS` was dead, so the token could not be read and the gate could not be opened.
- `post-tool-dispatcher.py` had two separate readers of the file and both needed the guard. A hand check found only the first, because the shell still had `HOME` set and the second reader fell back to the real file. The parametrized test found the second.
- 31 tests: all five hooks against six malformed shapes, plus one that confirms the git gate still refuses while state is unusable. Degrading to "no state" must not degrade to "no enforcement".

### Fixed: a test expired overnight and the suite was red on main
- **`test_relative_age.py` pinned a frozen `NOW` and then called a function that reads the real clock.** A three day old timestamp rendered as "4d ago" once the date rolled over. It passed on the evening of 2026-10-02, when the suite was reported green, and went red at midnight.
- That one test now measures against the real clock, because `to_context()` does. The other 25 in the file pass `NOW` in explicitly and stay frozen. Its two intervals sit in the middle of their buckets so neither can turn over mid-run.

### Fixed: the Signal callout rendered a stored string as markup
- The concentration callout wrote the most repeated query straight into `innerHTML`. That string comes from telemetry exactly as a caller passed it, and the same field is escaped everywhere else in the file. The dashboard has no authentication and its own origin can call the lesson edit and delete routes.

### Changed: claims that could not fail now can
- **C26 asserted a substring and two comments satisfied it.** `test_C26_lesson_usage_is_actually_recorded` checked that `record_usage` appeared in the source of `query_lessons`. The v2.13 bridge fix added two comments naming it, so deleting the only real call left the test green. It now walks the function's syntax tree and requires an actual call. Confirmed by deleting the call and watching it fail.
- **New claim C33 compares every stated tool count against the server.** `static/architecture.html`, served to the operator by the dashboard, said 38 tools while the server exposes 50. Nothing checked it, because the number sits in prose rather than a version field. Ledger rows that pinned a count in passing had the number removed rather than updated, since a number in a sentence nobody checks rots again.
- **`test_database_deleted_mid_session` could not fail.** Its only check after deleting the database was `assert True`, inside an `except` branch that never ran. The real behaviour, now measured and asserted: reads keep working on the open file handle, the file is not recreated, and the next write raises.
- **The only test that reads the bridge sweep evidence skipped silently** when the file was missing, and resolved its path against the process directory rather than its own, so it also skipped when run from anywhere but the repository root. A missing evidence file now fails.

### Fixed: the test suite wrote to the operator's log
- `conftest.py` redirects `MGCP_DATA_DIR` to a sandbox and says the suite does not reach the operator's data. The log directory was hardcoded and read no environment variable, and `server.py` configures logging at import, so every test module that imported it attached a handler on the live log. Sandbox paths were written into real diagnostic history, which rotates at 10 MB keeping 5 backups. The log directory now follows `MGCP_DATA_DIR`, resolved per call so a test that sets it after import still lands in the sandbox. A full run now changes the live log by zero bytes.

### Fixed: a second writer of workflow step IDs validated nothing
- `update_workflow_state` gained store-backed validation yesterday, on the reasoning that the write is the only place an unresolvable ID can be caught. `schedule_reminder` writes the same field and checked nothing, so a reminder could tell the model to call `get_workflow_step` with an ID that resolves to nothing. It now validates the workflow, the step and every lesson ID before scheduling, and refuses with the list of real steps.
- The refusal for a workflow with no steps read "Its steps are: ." It now says so in words.

### Changed: measured numbers corrected
- **Raising the bridge floor substitutes appends; it does not only discard them.** The published claim was "drops 4 appends", read off the totals 30 and 26. Comparing the two runs note by note, 0.55 removes 6 and admits 2 that 0.25 never produced. A higher floor can add a result because the bridge keeps only the top 3 members of a matched community, so filtering out a low scorer promotes the next candidate into that window. The claim that the one vouched-for append survives still holds.
- CLAUDE.md said every count on the Signal view has a deduplicated twin. Only match quality does. It also stated the median change in a direction that read backwards, and quoted live-store figures with no date on them.
- The claim ledger's C06 row listed `/ws/events` as a verified endpoint after the commit that deleted it. The row now records why the check accepted it: it confirms a route exists, not that anything uses it.
- The backlog's entries for the missing bridge relevance floor and the undocumented apology gate are struck through, since both shipped.

### Changed
- `hook_templates/VERSION` moves to 2.16. Installed hooks upgrade on the next `mgcp-init` run, and the gate change is in the hook, so it takes effect only after that.


### Removed: a push channel nothing listened to
- **Deleted the WebSocket subsystem from the web server.** `/ws/events`, `ConnectionManager`, and the background task that polled the telemetry database every 500 milliseconds to feed it. The instrument panel has no WebSocket client, and neither did the eight pages it replaced in v2.13.
- The poll task ran for the life of every dashboard process. It woke twice a second, checked for connected clients, found none, and slept again.
- **Two documents claimed this worked.** The README API table listed `WS /ws/events | Real-time events`, and `architecture.html` described the dashboard as having WebSocket updates. Both were true in the sense that the route existed. Claim test C06 checks that documented endpoints resolve to a route, which is why this passed for months: the test asks whether the endpoint exists, not whether anything uses it.
- `TelemetryLogger.subscribe`, `unsubscribe`, the `_subscribers` list and the notify loop in `_emit` went with it. Those existed only to feed that endpoint.
- Three dependencies had zero imports anywhere in `src/` or `tests/` and are removed: `websockets` (needed only to serve the route), `playwright`, and `aiohttp`. The screenshots in `docs/screenshots/` are committed images with no generator script, so nothing used playwright.

### Removed: an archive that existed to satisfy its own test
- **Deleted `examples/claude-hooks/legacy/`**, 599 lines across six superseded hook scripts. Nothing imported, executed, installed or compared against them.
- `test_legacy_hooks_archived` asserted that three of those files existed. That was the only reader. The test protected the archive and the archive existed to satisfy the test, so both are gone and git history holds the files.
- The sibling test in the same class is kept and now explains itself. `test_settings_json_empty` asserts this repository's own `.claude/settings.json` stays `{}`, because a committed project-local hook registration would fire a second time on every event alongside the global one.
- `tests/test_init_project.py` keeps its legacy-hook tests. Those exercise the scrub that removes stale hook references from `settings.json`, and they write their own fixture files, so they never read the archive.

### Changed: two report printers became tests
- `test_benchmark_report` and `test_category_breakdown` in `tests/test_intent_benchmark.py` computed a three-way classifier comparison, printed a table, and asserted nothing. They could only fail by raising.
- Both now assert the conclusion they print, which is the one that decided where intent classification lives. Macro F1 measured at regex 0.69, graph-community 0.23, LLM 0.92, and exact match at 58%, 2% and 87%. The breakdown asserts the shape of the gap: regex matches the LLM on directly worded messages, 20 of 20 against 19 of 20, and collapses on indirect phrasing, 1 of 15 against 13 of 15.
- `classify_llm` reads committed blind classifications, so there is no API call at test time and no run-to-run drift. The assertions were verified by breaking them: two tests fail when the bounds are tightened past the measured values, and all 19 pass when restored.
- A third check guards the rejected option. Graph-community classification is asserted to stay below 0.40 macro F1, so if it ever improves, the decision to drop it gets revisited instead of silently standing.

### Fixed: .gitignore contradicted the repository
- `CLAUDE.md` was listed in `.gitignore` with a comment saying users create their own. This repository ships one, `test_claims.py` reads it, and the docs-sync rule requires updating it with every behaviour change. The rule only ever applied to untracked copies, so the file stayed tracked and the contradiction showed up in `git ls-files --ignored -c`.

### Notes on what was measured and left alone
- A scan of all 410 definitions in `src/mgcp` found one unreferenced name, the WebSocket route above. After the removal the same scan reports zero. Module-level constants with no reader: zero.
- `scripts/clean_tool_call_envelopes.py` looked unreferenced and is not. `models.py` and `server.py` both name it in error messages shown to the operator, and `tests/test_tool_call_envelope.py` loads it. Kept.
- `tests/benchmark_data/run_blind_llm.py` looked unreferenced and is not. It regenerates the classifications that `test_intent_benchmark.py` reads. Kept.
- **The largest remaining duplication is left in place on purpose.** `tests/test_data_ops.py` repeats a two-patch mock scaffold 14 times, about 120 lines, varying only in the lesson list, which extra async mocks are set, and the search return. One context manager would collapse it. It is not done because a mock setup that reads top to bottom is easier to debug than one assembled from parameters, and 120 lines of test boilerplate is a worse trade than 14 indirections. Recorded here so the next pass does not have to re-measure it.


### Fixed: an apology already paid for could re-arm the gate
- **A message you send while a turn is still running used to re-fire the apology gate on an apology that had already been answered with a lesson.** Diagnosed from a live transcript: the sentence behind two real refusals was "You're right, I narrated it instead of doing it", written and answered in the preceding turn.
- Two mechanisms disagreed about where a turn begins. `UserPromptSubmit` fires for a mid-turn message and clears `turn_tools_called`, which is the only record that `add_lesson` already satisfied the gate. The gate's own backwards walk through the transcript breaks on a user entry, and a mid-turn message is written as `queue-operation` and `attachment` entries, never as `type == "user"`. So the state said "new turn, nothing called yet" while the transcript said "same turn, apology still present".
- Both halves now key off `turn_started_at`, written by the same hook call that clears the tool list, so they cannot disagree. An entry with no readable timestamp is passed over rather than treated as old, so a transcript format without timestamps behaves as before, and a missing or malformed value means no bound at all. The gate keeps refusing in every one of those cases, because its fallback has to be the strict direction.
- 6 tests, one of which removes the bound and checks the old bug still reproduces, so the fix is attributed to the bound and not to something else in the state.

### Added: the gate records what it fired on
- **Every refusal and compliance now records the matched pattern and the flagged sentence.** Until now only a contested fire carried the sentence, because the model supplies it when disagreeing, which left the model choosing the evidence for every record that had any.
- The first nine weeks of the log hold 39 refusals with no sentence and 16 verdicts with one, and all 16 verdicts were the model ruling in its own favour. Whether the gate is right could not be answered from a log like that, because answering it needs the text and the text was discarded. The refusal is the only place that text exists.
- The sentence is capped at 300 characters, since the log is append-only. A known false-positive class is now visible rather than invisible: the patterns match on word boundaries with no idea who is speaking, so quoting someone else's apology reads as making one. A test records that behaviour instead of asserting it away. A matcher that judged meaning was built and removed before for scoring worse in both directions.
- Counting still has to be done by a person, and the first measurement will cover records written from v2.15 onward.

### Changed: the bridge appends less for the same benefit
- **`BRIDGE_MIN_SCORE` moves from 0.25 to 0.55**, after the first sweep it has ever had. Appends fall from 30 to 26 on the labelled query set with no change to the one append an annotator vouched for, and no change to the top three or to the hard negatives.
- Every value from 0.25 to 0.45 produced an identical 30 appends, so 0.30 of the old range did nothing at all: the candidates the bridge considers all score above 0.45. 0.55 is the first value that changes anything, and 0.60 drops the useful append too.
- **Stated plainly: the four appends this discards are unlabelled, not known useless.** The labels were pooled from a 238-note snapshot and the store has grown since, so this buys a smaller context window on thin evidence. It does not claim those four were worthless, and it does not change the finding that an appended note cannot reach the top three.
- `tests/bridge_benchmark.py` takes `--sweep`. Each threshold runs in its own process against its own copy of the store, because the bridge ranks by use count and `query_lessons` writes use count, so one copy cannot host two conditions. Every row is checked against the off run: the searched results must be identical at every threshold, and the harness voids any row where they are not. Three tests read the recorded sweep rather than restating its numbers, so changing the shipped value without re-running the sweep fails.

### Changed: usage figures are reported twice, and the two disagree
- **The published median match was 0.691 and is 0.608.** The first counts queries, and the hooks issue some of the queries they are being measured by. The git gate mandates `query_lessons('git commit')` before any commit, so that one string is 521 of 1,161 recorded queries in the live store, and one note wins 517 of them at a nearly constant score, which is also the median of the whole corpus.
- `/api/signal` returns a `concentration` block: distinct questions, how many were asked exactly once, the most repeated strings with their share, and match quality recomputed with each different question counted a single time.
- The Signal view leads with the deduplicated figure and keeps the raw one as its sub-label, with a callout naming the repeated string and its count. **Neither number is suppressed.** The repeated query really is asked that often and really does matter each time, which is what the raw figures measure. The gap between the two is the share of the evidence the system generated for itself, and no single number shows it.
- 5 tests, including one that holds the two figures apart: a metric that cannot disagree with the raw count is decoration.

### Fixed: workflow state accepted step IDs no workflow contains
- **The hook spent this whole session injecting "EXECUTE step 'implement-fix' now" for a step that does not exist.** The `bug-fix` workflow's steps are reproduce, investigate, fix and verify. `get_workflow_step` answered "not found" every turn, and nothing could end it, because the state is what the hook replays and nothing validated the state.
- `update_workflow_state` now checks the workflow and both step IDs against the store before writing anything, and a refusal lists the steps that workflow does have. Standing a workflow down with `workflow_complete` deliberately skips validation, since that is the exit and it cannot depend on the store agreeing.
- **A call that changes nothing now says so.** Every argument has a default, and a misspelled argument name is dropped before it reaches the function, so the old code answered "Workflow state updated" having written nothing. That is how this session convinced itself the tool had no exit when it did.
- 6 tests.

### Fixed: a test had been red on main
- `test_semantic_search_names_the_holder_and_the_remedy` asserted the unavailable-store message would name `MGCP_QDRANT_URL`. The multi-session commit replaced that with `mgcp-qdrant setup`, which is the better remedy, and left the test behind. Three further commits were pushed over the top of it, and the session reported the suite as green with a test count read from memory rather than from the run.
- The assertion now anchors on the command a reader can actually run. The lesson `a-skipped-run-is-not-a-passing-run` was widened to cover it: a check that did not run and a run whose output you did not read are both indistinguishable from a failure.

### Changed
- `hook_templates/VERSION` moves to 2.15. Installed hooks upgrade on the next `mgcp-init` run.


### Added: a clock on every turn
- **The first line of every turn is now the time**, as in `<time>⌚ 21:09 Fri 2 Oct · 6m since your last message · 48m into this session</time>`. A model reads a transcript with no sense of elapsed time, so a reply written three hours later reads the same as one written in ten seconds.
- Three parts, each earning its characters. The local time, because nothing else in the turn carries it. The gap since the previous message, because that is what decides whether "we just did that" is still true. The session length, shown once the session is a minute old.
- **Minute resolution here, unlike the age shown beside a note.** `relative_age` in the package calls anything under an hour "now", which is right for the age of a note and wrong for the gap between two messages. The hook carries its own formatter, and a test pins the two to the same output from one hour upward, so two formatters do not become two places for one bug.
- The clock is built before anything else in the hook and wrapped so a failure cannot take the rest of the injected block with it. A corrupt state file, a missing timestamp, a string where a number belongs, and a clock change that makes the gap negative all still produce a clock.
- A new session id discards the previous session's timings, because the state file survives a restart and those numbers would be wrong.
- `test_neutral_message_zero_output` needed updating. It stripped the one unconditional block and asserted nothing remained; there are now two. The property it guards is unchanged: a neutral message triggers no gate, no reminder, and no workflow text.
- `hook_templates/VERSION` moves to 2.14. Installed hooks upgrade on the next `mgcp-init` run, and until then the deployed copies keep their current behaviour.
- 25 tests in `tests/test_user_prompt_clock.py`, three of which run the hook as a subprocess and check that the clock is the first line printed, that the turn time is recorded for the next turn, and that the next turn shows the gap.


### Added: injected text says how old it is
- **Every result from `query_lessons` now shows the age of its wording**, as in `(relevance: 68%, 4h old)` or `(relevance: 67%, 8mo old)`. The age comes from `last_refined`, so a note rewritten last week reads as current whatever its creation date.
- **Active todos show how long they have been pending**, as in `⏳ [5] (8mo) Add BGE instruction prefix to queries`. Three todos in the live store turned out to be 8 months old, and nothing in the injected text had ever said so.
- The project header shows the interval since the project was last touched, and the journal header shows it beside the absolute timestamp it already printed.
- **Relative, not absolute, and that is the point.** An absolute timestamp makes the reader work out the interval, and working out intervals is the part that goes wrong. `relative_age` returns "now", "5h", "3d", "7mo" or "2y", and `age_phrase` adds "ago" or "old" where a suffix reads correctly.
- Measured cost: 116 characters, about 29 tokens, on a `query_lessons` response of 8,863 characters. Roughly one percent.
- This is injected text only. A separate measurement found that putting a date into the text that gets *embedded* does not help retrieval and costs a little precision, so the index is unchanged.
- **Three bugs, all in the arithmetic, all caught by checking the boundaries before shipping.** Truncation made 365 days read as "0y" and 730 days read as "1y". Appending a suffix to "now" produced "now ago" in the project header and "now old" on a note refined minutes earlier. Rounding the months then made 364 days read as "12mo", one line above the bucket that calls 365 days "1y", so the month value is capped at 11.
- 26 tests in `tests/test_relative_age.py` cover the whole ladder from one minute to three years, each of those three bugs, a naive timestamp read as UTC, a missing timestamp, a future timestamp, and the age reaching the rendered project text.


### Changed: the timestamp result does not transfer, and the report said so wrongly
- **Corrected a false statement in the report.** It said "MGCP does not record a timestamp on each note today". MGCP does. `created_at` and `last_refined` are set on all 305 notes in the live store, REM's stale-note scan reads both, and export and import carry them. The gap was only that the date is absent from the text that gets embedded and from the search payload.
- **Measured whether the date belongs in the embedded text. It does not.** The LoCoMo result showed that adopting their timestamped record format raised search accuracy there by about 3 points, which looked like a free improvement. LoCoMo asks 321 of 1,536 questions about dates, so a date in the stored text is something a fifth of its questions can match. MGCP's live trace holds 562 distinct questions over nine months, of which 1 contains a time word, and that one is "when to stop reviewing", which asks about circumstances.
- The A and B confirmed it. Two indexes over the same 305 notes, one with "Recorded on 7 January 2026." appended, scored on the 34 labelled queries at the shipped 0.30 limit. Rank-1 accuracy unchanged on both the calibration and the held-out sets, recall in the top 3 unchanged at 1.000, precision in the top 3 down 0.013 on the held-out rewordings, and 0.029 more irrelevant results shown per query. The cost is about one query's worth on a 34-query set, so its size is uncertain, and the direction agrees with the query distribution.
- `tests/timestamp_ab.py` holds the experiment. Each condition runs in its own process against its own copy of the store, because both build an index in the same place and the built-in index allows one program per directory.
- The dates are still absent from the search payload, which holds the note id, trigger, action, tags, and use count. That means results cannot be filtered or sorted by age. It is a filter rather than a retrieval change, and no query on record needs it, so it is recorded and not built.


### Added: two measurements that had not been run before
- **The score carries information about whether an answer exists.** LoCoMo holds 446 questions the conversation does not answer, where the right reply is to say so. Recall is meaningless on them, so they had been set aside. The question worth asking is whether the score itself separates them, since that is what a score filter acts on. MGCP reaches an AUC of 0.784 with a 95% interval of [0.760, 0.807], where 0.5 would mean no signal. DRAGON, the engine behind the paper's results, reaches 0.609. BM25 reaches 0.509, which is no signal, and over raw messages it reaches 0.436, which runs backwards because a trick question borrows wording from a related message.
- **The gap is tested, not eyeballed.** Both engines scored the same questions, so two separate intervals would be the wrong comparison. A paired interval resamples the questions once and scores both engines on that resample. MGCP over DRAGON is +0.176 with an interval of [+0.140, +0.210] on facts, and +0.129 [+0.088, +0.168] on raw messages. Every interval excludes zero. The two engines are level at finding the right message, and MGCP is better at indicating whether a right message exists.
- **This gives the score filter a target.** At 0.30, the shipped value, nothing is filtered. At 0.60, 40.6% of unanswerable questions are filtered for the loss of 9.9% of answerable ones. These are LoCoMo's scores, so the same measurement must be run on MGCP's own notes before the shipped value changes.
- **The link graph was measured for the first time.** `query_lessons` appends related notes after the searched results, and those appends were 31.8% of everything returned over nine months. Ledger row E05 had measured how they were ranked and never whether they help. Over the 34 labelled queries, with the graph on and off, 30 appends produced 1 note that search missed and a human marked relevant, and 0 reached the top 3. Search fills the first five places and appends start at the sixth, so an append can add to what the agent reads and cannot change what it reads first.
- Each condition of that test runs against its own copy of the store, because the mechanism ranks candidates by how often a note has been used and `query_lessons` records a use. One store would let the first run change the second. The harness also checks that the searched results are identical in both runs, and declares the comparison void if they are not.
- New programs: `tests/bridge_benchmark.py`, and `--abstention` and `--abstention-difference` in `tests/locomo_benchmark.py`. Both need no dataset. Evidence in `docs/bridge-results/` and `docs/locomo-results/`, write-ups in [docs/bridge-measurement.md](docs/bridge-measurement.md) and [docs/locomo-retrieval-eval.md](docs/locomo-retrieval-eval.md).
- **The statistics are checked before they are used.** `tests/test_locomo_benchmark.py` compares the rank-based AUC against a brute force count over every pair, on 300 random tie-heavy cases and 100 continuous ones, and checks McNemar against a case computable by hand. The first draft of that check failed because the expected value written by hand was wrong and the estimator was right. `tests/test_bridge_benchmark.py` covers the output parser, which is the part that could silently report that the link graph never fires.
- Both findings state their limits. The useful-append count is a lower bound, because an unlabelled append scores as useless, and 26 positive queries shows a direction rather than a rate.


### Added: a Measurements section in the README, and evidence anybody can rerun
- **The README said nothing about the LoCoMo results.** The work sat in `docs/` and in ledger row E12, with no link from the front page. There is now a Measurements section with the headline numbers, the six steps taken to keep the comparison fair, a table of where every artifact lives, and the two commands that reproduce it.
- **The paired statistics could not be reproduced from anything committed.** The report quoted p=0.18 and a set of confidence intervals that came from a script written inline during the session and saved nowhere. The files that fed it sat in a temp directory. A published claim of "too close to call" that nobody can rerun is not evidence.
- `tests/locomo_benchmark.py --compare` now does that work. It takes per-question files, computes recall, McNemar's exact test on the questions where two engines disagree, and a 95% interval from 10,000 resamples with a fixed seed. It needs no dataset, so anybody who clones the repository can rerun the test.
- **The per-question files are committed, with the question text hashed.** Those files are the evidence for the paired test, but LoCoMo's questions are CC BY-NC data. Each row now holds a 16-character hash of the question instead of the text. A hash is stable across runs, which is all the pairing needs, so the evidence can be committed without copying their data. 180 KB per file.
- **Corrected a number in the report.** It said the paired test ran on 1,536 questions. It ran on 1,525. Eleven questions appear twice in the same conversation, and a question cannot be paired with itself. The 1,536 figure was hardcoded in the throwaway script's output rather than counted, and making the test reproducible is what exposed it. Both numbers are right for what they count, and the report now says which is which.
- `test_E12` checks the committed paired files: 1,525 questions, the p-value the report quotes, and the recorded verdict. The write-up and the evidence can no longer drift apart.


### Fixed: a maintenance cycle no longer takes the search lock it does not need
- **`rem_run` opened a vector store before building the engine.** Only one of the seven operations, `duplicate_detection`, needs vectors. Opening one up front meant every cycle took the Qdrant lock, including the cycles `rem-required-before-commit` forces at commit time when nothing is due. The built-in search index allows one program per directory, so a session that never searched anything still held the lock for the rest of its life. That blocked the dashboard and any second session from reading the index.
- `rem_run` now passes a factory, and `_duplicate_detection` awaits it. The result is remembered, including a failure, so one cycle does not retry a failed open. An explicit `vector_store` still wins, which keeps the CLI and the existing tests working.
- A failed open no longer affects the other six operations. The warning names the reason, and `duplicate_detection` reports that it could not scan rather than reporting health it never measured.
- Measured in embedded mode after the change: a cycle with nothing due creates no Qdrant directory and no lock file. In server mode the process opens no connection to the server.
- Six tests in `tests/test_rem_scheduling.py`. Five cover the engine: a cycle without `duplicate_detection` never calls the factory, `duplicate_detection` does call it, a failed call is not retried, a failing factory leaves the other operations running, and an explicit store still wins. The sixth covers `rem_run` itself, which is where the defect was, and fails if the call site opens a store when nothing is due.


### Added: refine_lesson can change a trigger and tags
- **`refine_lesson` takes a `new_trigger`.** The trigger carries most of the weight in retrieval, so a lesson with the wrong trigger is never returned and never applied. Until now the tool could change the action and append to the rationale, which meant a mis-triggered lesson could be added to but not corrected. The only way to fix one was a direct write to the store or the web editor.
- Found by hitting it. A rule about writing style was not returned by `query_lessons("git commit")`, which is the query the commit gate forces, so the rule never reached the moment it was written for. Appending to its rationale did not help, because the trigger is what the search compares against.
- The replaced trigger is written into the version history. Why a lesson started or stopped being retrieved is otherwise unrecoverable, and that is the question you ask months later when a lesson has gone quiet.
- The return value says the trigger changed, because a caller who edits a trigger has changed which future questions reach this lesson, and that is easy to do by accident.
- Four tests in `tests/test_server_tools.py`: the trigger is replaced, the old one is kept in the history, an empty argument changes nothing, and a search for the new wording finds the lesson. The last one matters most. An edit that is not re-indexed changes nothing at all.
- **`new_tags` replaces the tag list.** Tags are part of the indexed text and they also drive tag-filtered search, so a wrong tag list is a second way to make a lesson hard to reach. Omitting the argument keeps the tags. Passing an empty list removes them all, because somebody may well mean that and the two cases cannot share one falsy check. Blank and duplicate tags are dropped.
- **Fixed while adding this: `refine_lesson` never updated the in-memory graph.** The graph node carries the trigger, action, and tags, and nothing refreshed them, so every refinement left the graph holding the pre-refinement copy until the next server start. Community detection reads those tags, and REM maps community tags onto intents, so a retag could be invisible to the job that acts on tags. One line, and a test that fails against the old code with the node still reading its original value.


### Added: a style rule the commit path checks
- **New seeded rule `commit-message-prose-style`.** It refuses any commit whose message contains an em dash. Prose in this repository follows ASD-STE100 and the Google developer documentation style guide, and the em dash used as a pause is the clearest sign a sentence was not written to either. The check reads `tool_input.command`, where the message arrives whether it comes through `-m` or a heredoc. Bypass with `MGCP_BYPASS:prose` when a message quotes an em dash on purpose.
- **The check is one character, on purpose.** A wider pattern would start refusing legitimate commits, and the rest of the style is the author's job. The lesson states the rest.
- **The lesson was unreachable at the moment it was needed.** `human-prose-no-ai-tells` was written on 2026-08-26 and is correct, but `query_lessons("git commit")` never returned it, because its trigger said nothing about commits. The git gate forces that exact query before every commit, so the advisory half of the rule never fired. The trigger now covers commit messages, README, CHANGELOG, documents under `docs/`, comments, and docstrings. It ranks first for "git commit" at 72% relevance.
- The `git_operation` intent now states the rule in the text the dispatcher injects on every message, so the requirement appears before the commit is drafted rather than after it is refused.
- All three live in `~/.mgcp`, so they apply to every project on the machine. The rule is also in `DEFAULT_RULES`, so a fresh install gets it.
- `refine_lesson` cannot change a lesson's trigger, and the trigger is the field that decides whether a lesson is ever found. Fixing this one needed a direct write. Worth an MCP tool.
- Tests: five in `tests/test_pre_tool_dispatcher.py` run the hook as a subprocess and cover a heredoc message, a `-m` message, a plain message, an em dash in an unrelated command, and the bypass. Two in `tests/test_enforcement.py` pin the rule to the shipped defaults and check that no default rule's own text contains an em dash. One did.


### Added: search quality measured against an outside test set
- **New program `tests/locomo_benchmark.py` and a report in [docs/locomo-retrieval-eval.md](docs/locomo-retrieval-eval.md).** Until now the only test of MGCP's search was 34 questions about notes written by the same person who wrote the questions. That cannot show whether search works on other people's material. LoCoMo is a public test set of ten long conversations, 5,882 messages, and 1,986 questions with the answer location recorded for each one.
- **Result: MGCP returns the correct message in its top five results for 68.0% of questions.** DRAGON, the search engine used in the LoCoMo paper, scores 66.9% on the same material. The gap is one question in a hundred, and a paired test says that is noise (p=0.18). Keyword search scores 52.4%. Both engines beat it by about 15 points, so the model is worth its cost.
- **We ran their search engine instead of quoting their results.** The paper reports how often a language model answers correctly after reading search results. We measure how often search returns the right message. Those are two different measurements. Our copy of DRAGON follows their `task_eval/rag_utils.py`, and all three engines read the same stored text, character for character.
- **Three choices changed the conclusion.** A paired test turned an apparent 1-point win into a tie. The report states the highest score the test allows, 97.5%, because some answers sit in messages that no stored fact names, and without that number 80.3% reads as a worse result than it is. Repeating one run moved the score by up to three questions in 1,536, because the search index is approximate and the graphics hardware does not repeat its arithmetic. Three questions is larger than two of the gaps in the table.
- **Found while doing this: the score filter does not filter anything.** MGCP drops results scoring below 0.30. On this test set no question returns an empty result at that limit, and the scores do not change until 0.50. On MGCP's own notes, questions that should return nothing now return something, because the store has grown from 238 notes to 304 since the limit was set. The limit needs measuring again. Also, a limit cannot move between search engines: 0.60 removes 10% of MGCP's answers and 99.6% of DRAGON's.
- **One prediction was wrong.** MGCP stores a note as a trigger and an action. A LoCoMo fact is a statement with no action, so we expected the format to lower the score. On facts it makes no difference. On raw messages it is better by 6.3 points, because short messages carry little meaning alone. Copying LoCoMo's timestamped format added about 3 more points, and MGCP records no timestamp on each note today.
- Ledger row **E12** holds the claim. `tests/test_claims.py::test_E12_...` compares the report to the saved results in `docs/locomo-results/`. It caught five numbers that had been typed from memory into the report instead of read from the saved run.
- The test data is not stored here. `locomo10.json` is licensed CC BY-NC 4.0, so it is downloaded by the operator and ignored by git. Results may be used in research, not in sales material.
- Not covered, and stated in the report: no answers are generated; MGCP's link graph does not run on imported data, and it supplies 31% of results in daily use; the 446 trick questions are scored separately, because their recorded message is the one that makes a wrong answer look plausible.

### Added: several sessions at once on a local install
- **New command `mgcp-qdrant`.** Version 3.0.0 added support for a Qdrant server, which is what lets more than one session share a store. It did not add a way to get one. The only route was a Docker container, and `mgcp-init` never mentioned it. A feature you can only reach through an undocumented manual step is not shipped.
- `mgcp-qdrant setup` downloads the official Qdrant program for your computer (macOS on Intel or Apple silicon, Windows, Linux), checks it against a recorded checksum, installs it under `~/.mgcp/bin`, starts it on `127.0.0.1` with telemetry switched off, and points MGCP at it. No container, no package manager, nothing to fetch by hand. `status`, `start`, `stop`, `install`, and `teardown` complete the command, and `mgcp-init --multi-session` does the same job.
- Qdrant publishes no checksum file, so the checksums are recorded in `qdrant_server.py`. They were produced by downloading every published archive at the pinned version. A file that does not match is not installed.
- **The server address now lives in `~/.mgcp/config.json` under `qdrant_url`.** This is what makes the feature reachable. An MCP server is started by your LLM client, so it inherits that client's environment. Typing `export MGCP_QDRANT_URL=...` in a shell never reaches it, and sharing a store means several such processes. `MGCP_QDRANT_URL` still wins when it is set, for a server you run yourself. The new `config.py` writes the file in one step, because the hooks read that directory on every tool call. A file that exists but does not parse raises an error, because using the embedded store while the operator believes they are on the server means two sessions writing to two stores with nothing to show it.
- **A session starts its own server if the configured one is down.** Without this, the first session after a restart loses search for its whole life while a server sits installed and idle. It only starts a server at MGCP's own address, it honours `MGCP_QDRANT_AUTOSTART=0`, and a failed start falls through to the existing "search unavailable" message. The dashboard does the same.
- **`mgcp-init` now reports which store the computer uses** and how to change it. Saying nothing was the real defect.

### Fixed: three problems that made the 3.0.0 feature unreachable
- **`mgcp-embed` was listed in `pyproject.toml` but was not installed.** An editable install does not pick up a new command until you run `pip install -e .` again. On the computer where version 3.0.0 was built and measured, the command in the README did not exist. The shared model never started, every process loaded its own 448 MiB copy, and the advertised 779 MiB saving had never once been in effect. `mgcp-embed --status` now reports `"mode": "daemon"`.
- **Our own instructions produced a version warning.** `qdrant-client>=1.12.0` installed version 1.16.2, while the README told users to run server version 1.19.1. The client refuses a gap wider than one minor version and warns on every connection. Both are now pinned, and a test fails if a future change moves one without the other.
- **The installer could not verify github.com on a python.org build of macOS**, which ships no certificate store. The first real run failed with `CERTIFICATE_VERIFY_FAILED`. `certifi` is now a listed dependency and the download uses it. There is no option to skip the check.

### Fixed: a locked search index no longer stops every tool
- **One locked store used to break all 50 tools, including the ones that need no search.** `_ensure_initialized` opened the Qdrant client in the same block as the SQLite store, the graph, and telemetry. When another MGCP process held the embedded Qdrant lock, the whole thing failed. From this repository's own log at `mgcp.log:23795-23799`: on 2026-10-01 at 10:58:25, :26, and :27 the three calls that the session-start hook requires all failed one second apart with `Storage folder ~/.mgcp/qdrant is already accessed by another instance of Qdrant client`. The operator had restarted the session to test version 3 from cold, and the previous session's server still held the lock. Two of those three calls read SQLite only. The session that followed ran with no memory, and it could not have saved any either.
- **Startup now opens each store on its own.** `_ensure_initialized` opens SQLite, the graph, and telemetry, and never touches Qdrant. `_ensure_vector_stores` opens Qdrant the first time a tool needs it. 26 of the 36 tools need SQLite only and now work whatever holds the lock: project context, the journal, reading notes by name, the graph, workflows, intents, and six of REM's seven jobs. The dashboard has worked this way since 2.13. The MCP server never did.
- **The error now says what to do.** `VectorStoreUnavailableError` names the process holding the lock, with its command line, and points at `mgcp-qdrant setup`. The message from Qdrant named neither the holder nor a remedy.
- **Writes continue instead of failing, because one of them is an escape route.** `add_lesson` is the only way to clear the apology gate. If a lock held by another process could block it, an armed gate would have no exit. `add_lesson`, `refine_lesson`, `add_catalogue_item`, and `save_community_summary` now save to SQLite and report "stored but not yet searchable". The next time the search index opens, it adds what it missed. `delete_lesson` and `remove_catalogue_item` still refuse, because deleting the record while its search entry survives leaves a result pointing at something that no longer exists.
- **`rem_run` passes nothing rather than a silent stand-in**, so its duplicate scan reports that it could not run. A stand-in would have brought back the false clean report fixed in 2.13.
- **Found by the linter, not by the author.** Sorting tools by their use of `vector_store` missed `rem_run`, which passes the store on as a keyword argument. Ruff's F821 caught the unbound name. That sort was the whole basis for "only 10 tools need search".
- `tests/test_failure_recovery.py::TestServerSurvivesLockedVectorStore` takes the lock from a separate process, as the real failure did. It checks that the three session-start calls answer, that the search error names a process and a remedy, that `add_lesson` still works and says its result is not yet searchable, and that a note written under the lock becomes searchable once the lock clears. All four fail against the old code with the original error.

### Fixed: a skipped maintenance run no longer reports good health
- **`rem_run` printed "No findings. Knowledge base looks healthy." when nothing had run.** The test was `if not report.findings`, which is also true when every job is skipped. The reassuring line came from the same branch whether the store had been checked or not. Seen on 2026-10-01: `Operations run: none` and `Knowledge base looks healthy` one line apart, at session 103, with nothing due. The misspelled-job version of this was fixed in 2.13. The nothing-was-due version survived.
- Three outcomes now read differently. Nothing ran says so, says the store is unchecked, and names a job you can force. A real run with no findings says what it checked, and says that is not a statement about what it skipped. Findings print as before. `tests/test_rem_scheduling.py::TestSkippedRunIsNotAPassingRun` covers the first two cases and both fail against the old code.
- Worth recording now that the report no longer hides it: `rem-required-before-commit` is satisfied by calling `rem_run`, so a run with nothing due still opens the commit gate. The gate forces attention, not maintenance.

### Changed
- **The README is no longer a second changelog.** Eleven sections named after versions 2.2 through 2.13 described releases that this file already records. Each release added a section instead of restating the current behaviour, which is the same drift that turned CLAUDE.md's hook table stale. The README now describes the system as it works today, with 3.0 and multi-session access as the one release it highlights, and a link here for the history.
- `tests/test_claims.py::test_C31_...` read the README between the headings `### v2.9` and `### v2.10`, so a claim about the apology gate depended on version headings. It now reads the section by its subject, and ledger row C31 points at the section instead of a version.
- **Prose written for this repository follows ASD-STE100 and the Google developer documentation style guide.** Short sentences, active voice, one idea per sentence, and every technical term defined where it first appears. This applies to the README, CHANGELOG, documents under `docs/`, commit messages, and code comments.

### Fixed: test tools that could not run in server mode
- **`tests/retrieval_benchmark.py` built its Qdrant client with a hard-coded path.** That is a fourth place in the code that creates a client, and the rule about one shared resolver did not cover it, because it sits in tests rather than in `src`. With `qdrant_url` set, the benchmark failed outright while the live store was readable over the network. It now uses `qdrant_client_args()`, `--qdrant-path` is optional when a server is configured, and the instructions no longer tell you to copy the store first.
- **`test_status_reports_mode_without_a_server` checked machine-wide state.** It asserted that nothing answers on `127.0.0.1:6333`. That held on the computer where it was written and failed as soon as a real server ran, which is the setup the feature exists for. The check is now stubbed, so the test covers what `status` reports.

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
