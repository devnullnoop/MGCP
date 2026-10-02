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

## What would settle it

Label the 29 appends that are currently unlabelled. Read each one against its
query and record whether it is relevant. That turns the lower bound into a real
rate, needs no code, and the 29 are listed in `docs/bridge-results/bridge-on.json`.
