# MGCP retrieval on LoCoMo

**MGCP's retrieval is statistically indistinguishable from the retriever behind
LoCoMo's published RAG results, and beats a lexical baseline by ~15 points.**
Measured 2026-10-02 on all ten LoCoMo conversations, 1,536 paired questions.

Everything below was measured by `tests/locomo_benchmark.py` on one machine, in
one run per cell. Nothing is quoted from the LoCoMo paper, for a reason given
under [Why we ran their retriever ourselves](#why-we-ran-their-retriever-ourselves).

---

## The question this answers

MGCP's own labelled set (`tests/benchmark_data/retrieval_queries.yaml`) is 34
queries over a corpus of imperative lessons written by one person. It is a
narrow instrument: it cannot say whether retrieval generalises to a corpus
somebody else built, in another register, at another scale. LoCoMo — ten
synthetic long-term conversations, 19-32 sessions each, 5,882 turns, 1,986
annotated questions — is an external published benchmark of the thing MGCP
claims to do, so it is the available check.

## Headline

hit@5, the regime that matters: an agent is injected five memories, not fifty.

| database | retriever | recall@5 | paired diff vs MGCP | McNemar p | verdict |
|---|---|---|---|---|---|
| **observation** (2,541 facts) | **MGCP** (BGE + query prefix) | **0.680** | — | — | — |
| | dragon (theirs) | 0.669 | +0.010 [-0.004, +0.025] | 0.18 | indistinguishable |
| | BM25 | 0.524 | +0.156 [+0.134, +0.178] | 5.5e-44 | **MGCP better** |
| **dialog** (5,882 turns) | MGCP | 0.628 | — | — | — |
| | dragon (theirs) | **0.632** | -0.005 [-0.026, +0.014] | 0.65 | indistinguishable |
| | BM25 | 0.479 | +0.147 [+0.119, +0.174] | 5.2e-25 | **MGCP better** |

The recall column is the committed run in `docs/locomo-results/`. The diff, CI
and p columns come from the paired run that exported per-question outcomes,
where the rates were observation 0.681 / 0.670 / 0.525 and dialog 0.626 / 0.631
/ 0.479 — up to three questions different, for the reason under
[What this is not](#what-this-is-not). The differences are computed within that
run, so they are paired; taking them across the two tables would not be.

Brackets are 95% bootstrap CIs on the paired difference (10,000 resamples,
seed 7). Both retrievers answer the same questions, so the sample is paired and
the test is McNemar's exact test on discordant pairs — a one-point difference in
a rate cannot be called a difference without one.

## Full metrics

MGCP, observation database, LoCoMo record format:

| set | n | R@5 | R@10 | R@25 | R@50 | MRR@10 |
|---|---|---|---|---|---|---|
| **all answerable** | 1,536 | 0.680 | 0.729 | 0.777 | 0.803 | 0.566 |
| 1 multi-hop | 282 | 0.723 | 0.798 | 0.883 | 0.908 | 0.537 |
| 2 temporal | 321 | 0.757 | 0.791 | 0.829 | 0.847 | 0.680 |
| 3 open-domain | 92 | 0.500 | 0.576 | 0.641 | 0.717 | 0.355 |
| 4 single-hop | 841 | 0.656 | 0.698 | 0.736 | 0.760 | 0.555 |

**Recall has a ceiling below 1.0.** In the observation database a fact carries
only the `dia_id`s its extractor recorded, so 2.5% of gold evidence is
unreachable however good retrieval is: any-evidence ceiling **0.975**, 750
distinct `dia_id`s reachable. R@50 of 0.803 is **0.823 of attainable**. The
dialog database has a 0.997 ceiling because each record is exactly one turn.

**Complete evidence is much harder than any evidence.** all-evidence@5 is 0.537
against recall@5 of 0.681 — that gap is the honest multi-hop result, since
multi-hop questions cite several turns and "found one of them" flatters them.

## Three findings

**The similarity floor is inert.** MGCP ships `min_score=0.30`. On this corpus
`returned-nothing` is 0.000 at 0.30 and recall is flat to 0.50 in every
condition; the floor only bites at 0.55-0.60. Taken with the separate finding
that hard negatives are no longer rejected at 0.30 on MGCP's own grown corpus
(238 → 304 lessons since that floor was calibrated), **the floor is currently
below the operating range of both corpora and filtering nothing anywhere.**
That is the most actionable result here and it is about MGCP, not LoCoMo.

**Score scales are not transferable.** dragon's cosine scores are compressed:
median top-1 0.573 against MGCP's 0.689. A floor of 0.60 costs MGCP 10% of its
answers and costs dragon 99.6% of them. Any tuned threshold belongs to the model
it was tuned on; moving one between retrievers is meaningless.

**MGCP's imperative schema is not a handicap, which is the opposite of what was
predicted.** A LoCoMo fact is declarative and has no "action", so wrapping it in
MGCP's storage template (`Trigger: …\nAction: …`) was expected to dilute the
embedding. On facts it is a wash (0.647 vs 0.652 bare, pre-timestamp run). On raw
turns the template is **better by 6.3 points** (0.577 vs 0.514) — short
utterances like "Hey Mel! Good to see you!" embed poorly alone and the framing
helps. The template does suppress absolute scores, which is why it interacts
with the floor.

Separately: **timestamps are worth ~3 points.** Adopting LoCoMo's own record
format, `(1:56 pm on 8 May, 2023) Caroline said, "…"`, moved MGCP's observation
R@5 from 0.652 to 0.680. MGCP's ingestion records no per-record timestamp today.

## Why we ran their retriever ourselves

LoCoMo publishes **QA F1**, not retrieval recall. Comparing our recall@k to
their F1 would compare two different measurements and call it a result. Their
`scripts/evaluate_rag_gpts.sh` names the retriever behind their RAG rows —
`dragon`, at top-k ∈ {5, 10, 25, 50} over `--rag-mode` dialog/observation/summary
— so the honest comparison was to run that retriever here: same records, same
questions, same gold labels, same scorer, one machine.

`dragon` is replicated from `task_eval/rag_utils.py::get_context_embeddings`:
`facebook/dragon-plus-context-encoder` and `-query-encoder`, CLS pooling on
`last_hidden_state[:, 0, :]`, L2-normalised, cosine. Their other encode helper
leaves the normalisation commented out; the one that builds the context database
for the published rows normalises, so that is what is replicated.

Every retriever sees byte-identical record text. The MGCP-template condition is
reported separately because it is MGCP-specific storage, not a corpus difference.

## What this is not

- **Not a QA result.** No answers are generated and no F1 is computed. This
  measures whether the right memory is retrievable, not whether a model then
  uses it correctly. That is Phase 2 and it needs a generator.
- **Not all of MGCP.** The community bridge never fires: it appends graph
  neighbours and needs a curated relationship graph, which ingesting LoCoMo does
  not produce. In the live store it is 31% of retrieved slots, so a real MGCP
  session's behaviour differs from this measurement in a way this cannot size.
- **Not a verdict on adversarial questions.** All 446 category-5 items are
  excluded from recall. They carry `adversarial_answer` instead of `answer` and
  their evidence points at the turn that makes a wrong answer look plausible;
  the correct QA behaviour is abstention, so retrieving that turn is neither
  right nor wrong at this layer. Their numbers are reported separately in the
  raw JSON.
- **Image content reaches no pipeline.** 1,226 turns carry a `blip_caption`, but
  their retrieval code keys on `img_file`, which this data release does not
  contain. Image-grounded questions are unanswerable for every retriever here,
  equally, including theirs.
- **One run per cell, one machine**, and the cells are not bit-stable. Two
  independent runs of the same configuration differed by one question in 1,536
  (up to three: MGCP 0.680 vs 0.681 in observation, 0.628 vs 0.626 in
  dialog): Qdrant's HNSW search is
  approximate and its graph depends on insertion order, and MPS matmul is not
  bit-reproducible either. The p-values in the headline table come from the run
  whose per-question outcomes were exported, so the two tables here differ in
  the third decimal. No conclusion turns on a margin that small — which is the
  point of testing the margin rather than eyeballing it. BM25 is deterministic.

## Reproducing

The dataset is **not vendored**: `locomo10.json` is CC BY-NC 4.0 (Maharana et
al., *Evaluating Very Long-Term Conversational Memory of LLM Agents*).
Non-commercial — results here are publishable as research, not as product
material.

```bash
curl -sLO https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

for r in mgcp dragon bm25; do
  for mode in observation dialog; do
    .venv/bin/python -m tests.locomo_benchmark \
      --data-file locomo10.json --retriever $r --mode $mode \
      --record-format locomo \
      --out-json docs/locomo-results/cmp-$r-$mode.json \
      --out-per-question /tmp/pq-$r-$mode.json
  done
done
```

The harness builds its own throwaway MGCP instance and **refuses to run against
`~/.mgcp`**: 2,541 strangers' facts in a 304-lesson curated corpus would change
every number in the benchmark this is meant to complement.

Aggregate results for each cell are committed under `docs/locomo-results/`.
Those are our measurements, not LoCoMo content.
