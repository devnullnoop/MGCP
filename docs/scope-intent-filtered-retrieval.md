# Scope: intent-filtered retrieval

> **OUTCOME: BUILT, MEASURED, REJECTED — 2026-07-29.** The hypothesis was
> wrong and the measurement is below. This document is kept as the record of
> *why*, so the idea is not rebuilt from first principles by the next person
> who notices that the router discards its own classification.
>
> **Measured on the 26 labelled positives, intents classified from query text
> alone and written to `tests/benchmark_data/query_intents.yaml` BEFORE the
> run:**
>
> | | P@1 | P@3 | R@3 |
> |---|---|---|---|
> | unfiltered | **1.000** | **0.551** | **0.766** |
> | intent-gated | 0.538 | 0.269 | 0.288 |
>
> Worse on every measure. Nearly half the time the correct lesson is not even
> in the gated set.
>
> **Why, and this is the part worth keeping:** intent classifies the SITUATION
> the caller is in; tags classify the LESSON'S SUBJECT. They are different
> taxonomies. `qdrant-lock` classifies as `catalogue_arch_note` — it is a
> gotcha — while its answer is tagged `task_start` because it is about
> debugging. Both are correct. Gating one on the other discards exactly the
> cross-category lessons that are most useful: a `git_operation` query whose
> right answer is a lesson tagged `testing`.
>
> A second, smaller cause: lessons added recently carry tags nobody has mapped
> to an intent, so gating excludes them outright.
>
> **What survived:** the tag filter was broken and is now fixed (§2a). That was
> a real bug found on the way to a wrong idea, and it stands on its own.
>
> **What was reverted:** the `intent` parameter on `query_lessons` and the
> `tags_for_intent` helper. Neither has a caller now, so neither ships.

---

**Filter by intent, then rank within the filter. Replaces the reranker/length-
normalisation recommendation, which is no longer needed.**

Drafted 2026-07-29. Supersedes the "the real fix is length normalisation or a
cross-encoder" note in `qdrant_vector_store.py`.

---

## 1. The idea

The system already classifies every message into one of 8 intents, then throws
that classification away and hands a free-text string to cosine similarity —
the exact regime measured as non-discriminating. Filtering by intent first
makes the ranking problem small enough that the noise stops mattering.

Why this beats the two fixes previously recommended:

- **Length-invariant by construction.** "git commit" and a fifteen-word
  description filter to the same candidates. The measured defect was that score
  tracks query *length*; a filter has no length.
- **Solves the off-topic case for free.** `my sourdough loaf will not rise`
  classifies to no intent, matches no tag, returns honestly empty. That is the
  defect currently carried as a strict xfail in
  `tests/test_retrieval_threshold.py`, and it dissolves without touching the
  floor.
- **Shrinks the pool before ranking.** Sourdough scored 0.506 against 238
  lessons. Against the 17 tagged `git_operation`, ranking barely matters.
- **No second model.** No cross-encoder, no calibration that drifts as the
  corpus grows.

## 2. What is actually there, measured

| | |
|---|---|
| `tag_to_intent` in `~/.mgcp/intent_config.json` | **99 tags → 8 intents.** Real and populated. |
| Corpus reachable by an intent filter | **201 / 238 lessons (84%)** carry at least one mapped tag |
| Per intent | task_start 144 · catalogue_security 50 · catalogue_convention 31 · catalogue_arch_note 27 · session_end 19 · git_operation 17 · catalogue_dependency 5 |
| `QdrantVectorStore.search(tags=...)` | **exists and has never worked** — see below |
| `query_lessons` | takes no tag or intent argument |

### 2a. The blocker: the tag filter is broken

`qdrant_vector_store.py:117` and `:379` store tags as a comma-joined **string**:

```python
"tags": ",".join(lesson.tags),          # -> 'git,commits,workflow'
```

`:243` queries them as if they were a list:

```python
FieldCondition(key="tags", match=MatchValue(value=tag))   # -> matches 'git'
```

`MatchValue` is exact equality on the whole field, so `"git"` never matches
`"git,commits,workflow"`. Measured: `search("git commit", tags=[...7 git tags])`
returns `[]` while the unfiltered search returns `git-practices` at 0.685.

**Every tag-filtered search in this system's history has returned nothing.** No
caller noticed because no shipped caller passes `tags`.

## 3. The work

Four changes. Three are small; one is a reindex.

| # | Change | Where | Size |
|---|---|---|---|
| 1 | Store tags as a list, not a joined string | `qdrant_vector_store.py:117,379` | 2 lines + reindex of 238 lessons |
| 2 | `query_lessons` accepts `intent`, resolves it to tags via `tag_to_intent`, passes them to `search` | `server.py`, `intent_config.py` | ~15 lines |
| 3 | Routing table tells the agent to pass the intent it just classified | `intent_config.py` rendered prompts | prompt text only |
| 4 | Decide and implement the fallback (§4) | `server.py` | ~10 lines |

Change 1 is the prerequisite for everything else and is independently worth
doing — a filter that silently returns nothing is worse than no filter.

## 4. The design decision that needs an answer

**What happens when the filter is thin or empty?** Three options, and this is
the whole risk surface:

- **Hard gate.** Intent supplied → only those tags → empty means empty. Honest,
  and it is what makes the off-topic case work. But 16% of lessons carry no
  mapped tag and become invisible whenever an intent is passed.
- **Filter, then widen.** Try filtered; if fewer than N results, retry
  unfiltered. Preserves recall, but reintroduces the noise it was meant to
  remove, and "empty" stops meaning anything again.
- **Boost, not gate.** Rank filtered matches above unfiltered ones without
  excluding anything. Safest for recall, weakest for the off-topic case.

**Recommendation: hard gate, and fix the 16% instead of designing around it.**
Those 37 lessons are unreachable-by-intent because they are untagged or carry
tags nobody mapped — which is a data problem with an owner. `intent_calibration`
is the REM operation that detects exactly this drift, and it ran for the first
time since May two hours ago. Let it report, then map the tags.

Widening quietly is how the current situation arose: a mechanism that appears to
filter, silently doesn't, and nobody finds out for months.

## 5. Risks worth naming

- **The agent misclassifies.** A wrong intent gates to the wrong 17 lessons —
  worse than no filter. Mitigation: the classification is already being made
  and acted on for routing, so this adds no new failure mode; it only makes an
  existing one visible.
- **`tag_to_intent` goes stale.** New tags land unmapped and their lessons drop
  out of intent-filtered results. This is precisely what `intent_calibration`
  exists to catch. It is now running; use its findings rather than adding a
  second mechanism.
- **Reindex risk.** Change 1 requires re-embedding 238 lessons. Prove it on a
  copy of `~/.mgcp/qdrant`, keep the live run a deliberate act, and verify the
  count round-trips before and after.
- **This does not fix the floor.** 0.30 stays. Filtering makes the floor mostly
  irrelevant for intent-carrying queries; it does nothing for queries with no
  intent. Both facts should be in the source comment.

## 6. Done test

1. `search("git commit", tags=["git","commits",...])` returns `git-practices`,
   not `[]`. Non-vacuous: revert change 1 and it returns `[]` again.
2. `query_lessons("git commit", intent="git_operation")` returns only lessons
   from the 17, and `query_lessons("my sourdough loaf will not rise",
   intent="git_operation")` returns nothing.
3. The strict xfail in `tests/test_retrieval_threshold.py` starts passing when
   an intent is supplied — flip it and record why.
4. Precision@3 on the labelled set, intent-supplied vs not. Publish both. If
   filtering does not beat 0.56 unfiltered, this scope was wrong and should be
   abandoned rather than tuned.

## 7. Out of scope

- Length normalisation and cross-encoder reranking. If §6.4 shows the filter
  works, neither is needed; if it does not, revisit then.
- Raising `DEFAULT_MIN_SCORE`. Measured and rejected — see the comment in
  `qdrant_vector_store.py`.
- Tagging the 37 unmapped lessons. That is `intent_calibration`'s job and a
  separate, data-shaped task.
