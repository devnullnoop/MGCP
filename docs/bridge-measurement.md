# The link graph, measured

Over the 34 labelled queries, the link graph appended 30 lessons. One of them was
a lesson the search had missed and a human had marked relevant. None of the 30
reached the top three results. Measured 2026-10-02 against a copy of a 305-lesson
store.

Reproduce with `python -m tests.bridge_benchmark`.

## What is being measured

`query_lessons` runs a search, and then adds more. It searches the community
summaries, finds the group of notes that matches the question, and appends members
of that group which the search itself did not return. Those appended results are
recorded with a score of 0.0, because relevance did not match them.

The appends are not rare. Over nine months of real use, 2,355 of 7,397 returned
results arrived this way, which is 31.8%. Ledger row E05 found
that the mechanism ranked its candidates by how often each note had been used, and
then recorded a use on whatever it appended, so it was ranking by a number it
produced. That was fixed. Whether the appended notes are useful was never measured.

## Method

Two runs over the 34 labelled queries in
`tests/benchmark_data/retrieval_queries.yaml`. Each query carries one gold note
that should win, and a list of every note a human accepted as relevant.

One run has the link graph on, the other has it off. Setting `BRIDGE_MIN_SCORE`
above 1.0 switches it off without touching its code, because cosine similarity
cannot exceed 1.0.

**Each run gets its own copy of the store.** The mechanism ranks candidates by how
often a note has been used, and `query_lessons` records a use. One store cannot
host both conditions, because the first run would change the second.

The operator's store is copied and never opened.

## Result

Measured at a floor of 0.25, which was the shipped value at the time. The floor
is now 0.55, for the reason in "Choosing the floor" below, and at 0.55 the
appended count is 26 rather than 30. `bridge-on.json` holds the 0.25 run and
`bridge-off.json` holds the run with the bridge disabled, so "on" in those two
filenames means the floor this section measured and not the floor now shipped.

| Measure | Count |
|---|---|
| Positive queries that received appends | 14 of 26 |
| Appended results in total | 30 |
| Appends that supplied a relevant note the search missed | 1 |
| Queries where an appended note reached the top 3 | 0 |
| Hard negatives that received appends | 0 |

The one useful append was on the query `before-push`, which gained
`query-before-git-operations`.

**Validity check.** The searched results were identical in both runs, so the only
difference between them was the link graph. The harness reports the comparison as
void if that check fails.

## Reading this

The appended notes cannot reach the top three. The search is asked for five
results and returns five, and appends land after them. So an appended note starts
at position six at the earliest. It can add to what the agent reads, and it cannot
change what the agent reads first.

One useful append in 30 is the measured rate on this set. The cost is 30 extra
notes in the context window. The limits below bound how far that rate can be
read.

## What this does not settle

**The useful count is a lower bound.** A note nobody labelled counts as useless
here, and it might not be. The labels were pooled from a 238-note snapshot in July
2026 and the store now holds 305, so a genuinely useful append could be unlabelled
and scored as a miss.

**26 positive queries is a small sample.** This shows a direction. It does not
establish a rate. A second labelled set, or labels added for the 29 unlabelled
appends, would settle that.

**This is one corpus.** The notes, the queries, and the labels all come from one
person's store.

## Choosing the floor

`BRIDGE_MIN_SCORE` is the score a community member must reach against the query
to be appended at all. It was 0.25 and had never been tuned. Sweeping it over
the same 34 queries gives this, measured 2026-10-02:

| Floor | Appends | Useful | Unvouched | Queries hit | In top 3 |
|---|---|---|---|---|---|
| 0.25 (was shipped) | 30 | 1 | 29 | 14 | 0 |
| 0.35 | 30 | 1 | 29 | 14 | 0 |
| 0.45 | 30 | 1 | 29 | 14 | 0 |
| **0.55 (now shipped)** | **26** | **1** | **25** | **12** | **0** |
| 0.60 | 7 | 0 | 7 | 6 | 0 |
| 0.65 | 0 | 0 | 0 | 0 | 0 |

Reproduce with `python -m tests.bridge_benchmark --sweep`. Evidence in
`docs/bridge-results/bridge-sweep.json`.

Two things to read here. Nothing changes between 0.25 and 0.45, so 0.30 of the
old setting did nothing: every candidate the bridge considers scores above
0.45. And 0.55 is the first value that changes anything. It drops 4 appends and
keeps the one append a label vouches for, which is why it is now the shipped
value. 0.60 drops that append too.

**The evidence is thin and the change is small.** The four appends that 0.55
discards are unlabelled, not known-useless, so this buys a smaller context
window without proving the four were worthless. The append it protects is one
query. Every row is validated the same way as the on and off comparison: the
searched results are identical at all seven thresholds, so the bridge is the
only thing that differs, and the harness marks any row void where that fails.

This tunes the cost. It does not change the finding above: an appended note
still cannot reach the top three, and 1 vouched append in 26 is still the
measured rate.

## What would settle it

Label the 29 appends that are currently unlabelled. Read each one against its
query and record whether it is relevant. That turns the lower bound into a real
rate, needs no code, and the 29 are listed in `docs/bridge-results/bridge-on.json`.
