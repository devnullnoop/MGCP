# The apology gate: what worked, what failed

**A build log with the failures left in.** Written 2026-07-30, covering one
day's work on MGCP's apology gate — a PreToolUse hook that refuses tool calls
until the agent captures a lesson about the mistake it just acknowledged.

Everything below is drawn from commits, test output and audit logs in this
repository. Numbers that came from an agent's report rather than a re-run are
labelled as such.

---

## The scoreboard

| Attempt | Outcome |
|---|---|
| Semantic detection (embedding similarity) | **Failed.** Pre-registered, measured, rejected |
| NLI entailment detection | **Failed.** 20 configs, none separated |
| Attest-or-comply funnel, v1 | **Failed.** 6 confirmed defects, 2 catastrophic |
| Attest-or-comply funnel, reduced | **Shipped.** 920 tests + 50 direct checks |
| Agent-based verification, 3 attempts | **Failed.** Deadlocked by the gate under test |

Four failures, one ship. The ship is a strict subset of what was designed.

---

## What worked

### 1. The audit log — the highest-value component, by a distance

An append-only `~/.mgcp/gate_audit.jsonl` recording every denial (gate *and*
data rules), compliance, adjudication and human bypass.

It justified itself within two minutes of first deployment. Eleven denials
appeared in a 35-second window, all matching the same phrase, naming the tools
they blocked. That log turned "something feels wrong" into a diagnosis in
about ninety seconds. Before it existed, a denied tool call left **no trace at
all** — which is why no claim about enforcement could ever be graded from
evidence (recorded as ledger row E04 for months).

**Generalisable:** if a mechanism can refuse work, it must record that it did.
Not for compliance theatre — because you cannot debug an invisible decision.

### 2. Pre-registration killed two ideas cleanly

Before either detector was built, a falsification condition was written down:
*full separation between positives and negatives on a labelled set, with a gap
of at least 0.05.* Both detectors were then measured against a 35-sentence set
(18 positives, 17 negatives) built **before** any threshold was chosen.

- **Similarity:** negatives reached 0.750 while five true positives sat below
  that. No threshold exists. A contrastive variant failed in both directions.
- **NLI entailment:** five checkpoints × four hypothesis phrasings = 20
  configs. **Zero** achieved even full separation. The closest miss was
  degenerate — both classes collapsed toward P(entailment) ≈ 0.

Neither result was arguable afterwards, because the bar was set first. A blind
held-out set (12/12, authored by an agent that never saw a score) was prepared
for the confirmation stage and **never consulted** — nothing earned a shot at
it. It ships unused at `tests/benchmark_data/apology_sentences_holdout.yaml`,
and its value is precisely that it has never been calibrated against.

### 3. The diagnosis that survived both failures

Similarity encodes **topic**; NLI-as-trained anchors to apology **lexemes**.
The gate needs neither. It needs a **speech act**: *is this speaker
acknowledging their own error, in their own voice, right now?*

Concretely — `"I overlooked the second call site."` scored 0.0023 entailment,
while `"The user said sorry in the transcript we parsed."` outscored five true
positives. Every hard negative is on-topic; that is what makes them hard.

**Generalisable:** before reaching for a classifier, name the distinction you
need. If it is a speech act, a stance, or a commitment, off-the-shelf
similarity and entailment models will not carry it, and no amount of threshold
tuning changes that.

### 4. Reduction beat iteration

The shipped gate is v2.9's seven regexes **unchanged**, plus three additions.
Five other mechanisms were built, red-teamed, and deleted. That deletion is
not a retreat — it is the measured result. Each mechanism added to handle a
failure mode generated roughly two more.

---

## What failed

### 1. The escape valve became a universal off-switch

*The worst defect of the day, and it was mine.*

To fix a deadlock, a per-turn denial counter was added: after three denials
the gate would "degrade to advisory" and allow the call. The degrade path
called `sys.exit(0)` **43 lines before the rule-evaluation loop**.

Consequence: tripping the apology gate and absorbing three denials disabled
**every** data-driven enforcement rule for the rest of the turn — git gating,
sensitive-path protection, doc-coupling. Reproduced with no state tampering:
`git commit` with no `query_lessons` → **allowed**. And the bypassed rules
wrote no audit line, so the record did not even show enforcement had been
skipped.

The agent controls its own turn text, so it controlled whether the gate armed.
A safety feature had become a one-call disable of the safety system.

**Generalisable:** an escape hatch in one gate must not be an early return
from *all* gates. Ordering is a security property.

### 2. A malformed counter crashed the hook into a silent bypass

`int(counts.get(...))` was unguarded and `main()` had no top-level handler. A
non-numeric value raised, the process died at rc=1 with empty stdout — which
the harness reads as **allow**, writing zero audit lines.

`workflow_state.json` is agent-writable, so a single `Write` of
`"turn_gate_denials": "x"` disabled everything, invisibly. Worse than the
forgeable-counter problem it accompanied, because it required no correct
session id and left no trace.

Two more of the same class survived into the *reduced* version and were caught
by the final verification: a valid-JSON non-object state file (an array,
a string) parses cleanly and then raises on `.get()`; and a malformed
`session_id` normalised to `""`, which matched every caller — type confusion
into a global gate-opener.

**Generalisable:** any state an agent can write is hostile input. Guard types
at every read, and make fail-open **loud** — record the crash before allowing
the call, or the audit log is a fiction.

### 3. Cleverness in the detector broke the detector

A quote-and-code stripper was added so that documenting the gate would not arm
the gate. Its quote regex treated the straight apostrophe as a delimiter, so
two contractions blanked the text between them — and **`you're right` stopped
firing entirely.** The canonical trigger, silently disabled by a fix for a
cosmetic problem.

A companion "first-person subject" check split sentences on `.` only, so
third-person narration armed the gate whenever no period preceded it, and
genuine admissions failed to arm when a version number or filename appeared
first. It was also **vacuous for 6 of its 7 patterns**, since the pronoun it
looked for was inside the matched phrase.

**Generalisable:** each guard added to a matcher is a new place for it to be
wrong, and matcher bugs are silent by nature — nothing fires, and nothing
tells you nothing fired.

### 4. The gate deadlocked the work three times

Both exits are **tool calls**. In a harness that loads tool schemas on demand,
reaching `add_lesson` requires a discovery call first — which the gate was
also denying. The result was an agent locked in a room with the key inside;
only a human bypass freed it, and a subagent never receives a user prompt, so
for subagents there was no exit at all.

This was latent in v2.9 and became likely the moment detection widened. It
cost:

1. A verification workflow, blocked mid-run (11 denials, caught by the audit log)
2. A second workflow, degraded
3. A third workflow — **183,000 tokens, seven minutes, zero output.** The
   agents could not even call the tool that reports results. 51 denials across
   their transcripts.

The third failure is the cleanest demonstration of the defect anyone could
ask for, and it was entirely self-inflicted: the agents' prompts quoted the
trigger phrases, because their job was to test them.

**Generalisable:** if your gate's exit is itself gated, you have built a trap.
Check that the escape path is reachable *from inside the failure state* — and
if you are testing a live gate, the test instructions are input to it.

### 5. Deploying before verification

The widened detector was installed to the live machine at 11:34 and the
verification workflow launched at 11:36. It blocked real work within two
minutes. The correct order — build, verify green, *then* deploy — was skipped,
and it is now a lesson in the graph
(`verify-before-deploying-a-widened-detector`).

---

## What shipped

v2.9's seven regexes, unchanged, plus:

1. **Tool-discovery calls are never gated.** Stateless, exact-match. The
   entire deadlock fix, and it needed no new state.
2. **A second exit.** `adjudicate_apology_gate` records a verdict, the flagged
   text and ≥20 characters of reasoning. `not_apology` opens the gate for that
   session only; `apology` keeps it shut until the lesson is written — so
   attesting "genuine" is never a route around capture.
3. **The audit log**, with REM and SessionStart as its readers.

Plus type guards on every state read and a top-level handler that records
`hook_error` before failing open.

**Verification:** 920 pytest tests, and a 50-check direct harness against the
shipped hook covering data-rule independence, crash-open, exit reachability,
detection parity with v2.9, audit-log behaviour under an unwritable path, and
session scoping. The direct harness exists because the agent-based one
deadlocked; it is the more trustworthy of the two anyway, since no agent could
misreport it.

**Known and accepted:** quoted or reported apologies arm the gate — *"the user
said sorry"* fires it. The stripper that fixed this cost more than the problem;
the contest exit absorbs it in one call, on the record. And `add_lesson` in
`turn_tools_called` is global, so one session complying opens another's armed
gate. That is pre-existing v2.9 behaviour; fixing it means changing what
PostToolUse writes, and today was a thorough lesson in the cost of extra
surface.

---

## The five transferable rules

1. **If it can refuse work, it must record that it did.** An invisible
   decision cannot be debugged, and an unrecorded denial cannot support any
   claim you make about your own system.
2. **Write the falsification condition before the code.** Two detectors died
   quickly and without argument because the bar predated the numbers.
3. **Name the distinction before choosing the model.** Topic, stance and
   speech act are different problems. Similarity and entailment carry the
   first; nothing off the shelf carried the third.
4. **Check that the exit is reachable from inside the failure.** A gate whose
   escape hatch is behind the gate is a trap, and it will find you.
5. **State an agent can write is hostile input, and fail-open must be loud.**
   Guard types at every read; record the crash before you allow the call.
