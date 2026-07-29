# Scope: semantic tier for the apology gate

**OUTCOME: REJECTED, 2026-07-29.** Measured against a falsification condition
declared before any wiring was written. Do not rebuild with the same method;
the failure is structural, not a tuning problem.

## The intent

The v2.9 apology gate detects acknowledged failure with seven word-boundary
regexes. The operator's design intent was semantic matching — "that was an
oversight on my part" should fire the gate, and today it does not. The
architecture was ready: the BGE model sits resident in the MCP server all
session; a unix-socket sidecar would have let the stdlib-only PreToolUse hook
query it with a 300ms timeout and regex fallback. The sidecar and hook design
were sound. The classifier was not.

## Falsification condition (declared first)

> If BGE cosine cannot separate apology sentences from hard negatives on a
> labelled set, stop, keep the regex, record the negative result.

Set: `tests/benchmark_data/apology_sentences.yaml` — 18 positives (5
regex-visible, 13 paraphrases) and 18 negatives (work narration, error-talk
without self-blame, regex-trigger words in non-apology use, apologies as a
subject). Built before any threshold was chosen.

## Measurement 1 — max cosine vs apology prototypes (threshold classifier)

Symmetric `embed()` both sides, sentence max-scored against 6 apology
prototypes.

| band | min | max |
|---|---|---|
| positives | 0.643 | 0.892 |
| negatives | 0.420 | **0.750** |

**No separating threshold exists.** "The user said sorry in the transcript we
parsed" (negative) scores 0.750 — above five true positives, including "My
mistake, the flag was inverted" (0.658). Interleaved band width: 0.107.

## Measurement 2 — contrastive nearest-class (apology vs non-apology prototypes)

Score = max-sim(apology protos) − max-sim(contrast protos). Ship bar declared
before running: full separation AND gap ≥ 0.05.

- Lowest positive margin: **−0.003** ("My bad — fixing it now" — a true
  positive the *regex* catches, misclassified by the semantic tier)
- Highest negative margin: **+0.052** ("The correct answer is 42")
- Gap width: **−0.055.** Falsified, and now failing in both directions.

## Why (the keeper)

Sentence-similarity embeddings encode **topic**. Every hard negative is
on-topic — errors, fixing, the word "sorry" in reported speech. The
distinction the gate needs is a **speech act**: *is the speaker apologizing
for their own error, in this utterance, in their own voice?* Topic geometry
does not carry voice or commitment. This is the same taxonomy mismatch as the
rejected intent-filtered retrieval (`docs/scope-intent-filtered-retrieval.md`):
there, intent classified the situation while tags classified the subject;
here, similarity classifies the subject while the gate needs the act.

A vindication worth stating: the keyword tier is not a compromise
implementation of the semantic idea — on this task it **beats** the semantic
implementation, which both misses regex-visible positives and fires on
bystander sentences.

## What would plausibly work (future scope, not built)

- **NLI / entailment**: premise = turn sentence, hypothesis = "The speaker is
  apologizing for their own mistake." Entailment models are trained on
  exactly the voice/commitment distinctions similarity lacks. Cost: a second
  model resident in the server.
- **The LLM as classifier at session boundaries** (REM-style, advisory):
  post-hoc transcript scan for missed capture moments — no gate latency, no
  new model, but no enforcement either.
- Not worth building: more prototypes, other thresholds, prefix asymmetry.
  Both measurements above are reproducible from the yaml in ~30 lines; run
  them again only if the *model class* changes.

## Residue that ships

The labelled set stays (`tests/benchmark_data/apology_sentences.yaml`) — it is
the acceptance test for any future attempt. The sidecar/hook design is
recorded here and costs nothing while unbuilt. The regex tier stands, with its
dodge-risk documented in README §v2.9.
