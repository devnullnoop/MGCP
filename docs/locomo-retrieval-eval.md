# MGCP search quality, measured against LoCoMo

MGCP finds the right memory about as often as the search engine used in the
LoCoMo research paper. Both find it far more often than a keyword search does.

Measured on 2026-10-02, on all ten LoCoMo conversations and 1,536 questions.

## What this document is for

MGCP stores notes and finds them again later. The only test of that search was a
set of 34 questions written by one person, about notes written by the same
person. That test cannot tell you whether the search works on material from
somewhere else.

LoCoMo is a public test set from a research paper. It contains ten long
conversations between two people. Each conversation runs for 19 to 32 sessions,
and 5,882 messages in total. The authors wrote 1,986 questions about those
conversations. For each question they recorded which messages contain the answer.

That recorded answer location is what makes the test useful. You can ask MGCP a
question, look at what it returns, and check whether the right message is in the
results.

## Words used in this document

**Search engine.** The software that turns text into numbers and compares them.
MGCP uses a model called BGE. The LoCoMo authors used one called DRAGON. Both
work the same way. They score every stored item against the question and return
the highest scores.

**recall@5.** The share of questions where the correct message appears in the top
five results. A score of 0.68 means 68 questions in every 100 worked.

**BM25.** Keyword search. It counts shared words and gives more weight to rare
words. It uses no model and no training. It is the baseline that any search
engine should beat.

**p-value.** The chance of seeing a difference this large if the two systems are
equally good. A p-value of 0.18 is high, so that difference means nothing. A
p-value below 0.05 is the usual line for calling a difference real.

**Confidence interval.** The range the true difference probably sits in. If the
range includes zero, the two systems might be equal.

## Results

Each row answers one question. How often does the correct message appear in the
top five results?

| Stored as | Search engine | recall@5 | Difference from MGCP | p-value | Conclusion |
|---|---|---|---|---|---|
| 2,541 extracted facts | **MGCP (BGE)** | **0.680** | | | |
| | DRAGON (the paper's) | 0.669 | +0.010 [-0.004, +0.025] | 0.18 | Too small to call |
| | BM25 (keywords) | 0.524 | +0.156 [+0.134, +0.178] | 0.0000 | MGCP is better |
| 5,882 raw messages | MGCP (BGE) | 0.628 | | | |
| | DRAGON (the paper's) | 0.632 | -0.005 [-0.026, +0.014] | 0.65 | Too small to call |
| | BM25 (keywords) | 0.479 | +0.147 [+0.119, +0.174] | 0.0000 | MGCP is better |

MGCP and DRAGON are level. The gap between them is one question in a hundred,
and the test says a gap that small is noise. Both beat keyword search by about
15 questions in a hundred.

The recall column comes from the run saved in `docs/locomo-results/`. The
difference, interval, and p-value columns come from a second run. That run saved
the outcome of each question one at a time, which is what the statistical test
needs. In that run the scores were 0.681, 0.670, and 0.525 for the facts, and
0.626, 0.631, and 0.479 for the raw messages. The two runs differ by up to three
questions. The section on limits explains why.

## More detail on MGCP

The LoCoMo authors sorted their questions into four kinds. MGCP handles them at
different rates.

| Question kind | Count | Top 5 | Top 10 | Top 25 | Top 50 |
|---|---|---|---|---|---|
| **All** | 1,536 | 0.680 | 0.729 | 0.777 | 0.803 |
| Needs two or more messages | 282 | 0.723 | 0.798 | 0.883 | 0.908 |
| Asks about a date or time | 321 | 0.757 | 0.791 | 0.829 | 0.847 |
| Needs outside knowledge | 92 | 0.500 | 0.576 | 0.641 | 0.717 |
| Answered by one message | 841 | 0.656 | 0.698 | 0.736 | 0.760 |

Two numbers need care.

A perfect score is not 1.000. The facts in this test were extracted by the
LoCoMo authors, and each fact records which messages it came from. Some messages
are named by no fact at all. Those answers cannot be found, however good the
search is. The highest score available is **0.975**. MGCP's top-50 score of
0.803 is 82 percent of what the test allows.

Finding one correct message is easier than finding all of them. Some questions
need two or more messages to answer. MGCP returns every needed message for 53.7
percent of questions in the top five, against 68.0 percent for at least one.

## What we learned about MGCP

### The score filter does not filter anything

MGCP throws away search results that score below 0.30. On this test set that
limit removes nothing. No question returned an empty result at 0.30, and the
scores do not change until the limit reaches 0.50.

The same limit has also stopped working on MGCP's own notes. When the limit was
chosen, the store held 238 notes. It now holds 304. Questions that should return
nothing now return something. The limit is too low for both sets of material,
and it should be measured again.

### A score limit cannot move between search engines

DRAGON's scores sit lower than MGCP's. The middle score for the top result is
0.573 for DRAGON and 0.689 for MGCP. A limit of 0.60 removes 10 percent of
MGCP's answers and 99.6 percent of DRAGON's. A limit belongs to the model it was
measured on.

### MGCP's note format helps, which we did not expect

MGCP stores a note as a trigger and an action, which is "when this applies" and
"what to do". A LoCoMo fact is a statement, so it has no action. We expected that
mismatch to lower the score.

It does not. On extracted facts the format makes no difference, 0.647 against
0.652. On raw messages the format is better by 6.3 points, 0.577 against 0.514.
Short messages such as "Hey Mel! Good to see you!" carry little meaning on their
own, and the added framing helps.

### Timestamps are worth about three points

The LoCoMo authors store each message with the date and time of the session.
Copying that format raised MGCP's score from 0.652 to 0.680. MGCP does not record
a timestamp on each note today.

## Why we ran their search engine instead of quoting their results

The LoCoMo paper reports how often a language model answers correctly after
reading the search results. We measure how often the search returns the right
message. Those are two different measurements, and putting one next to the other
would not be a comparison.

The paper's scripts name the search engine behind their published results. It is
DRAGON, used over either the extracted facts or the raw messages. So we ran
DRAGON here, on the same stored items, with the same questions and the same
scoring. Every number in this document was produced by one program on one
machine.

Our copy of DRAGON follows their `task_eval/rag_utils.py`. It uses their two
models, takes the first output vector, scales it to length one, and compares by
cosine similarity. Their file holds a second copy of this code with the scaling
switched off. We follow the copy that builds the database for their published
results.

All three search engines read the same stored text, character for character.
MGCP's note format is reported as a separate line, because it changes the text
and that would make the comparison unfair.

## Limits

This measures search only. It does not measure answers. No language model reads
the results and writes a reply, so no number here compares to the paper's
headline results. That needs a second stage of work.

**This is not all of MGCP.** MGCP also adds related notes from its link graph.
That feature does not run here, because importing LoCoMo creates no links. In
daily use it supplies 31 percent of returned items, so a real session behaves
differently from this test in a way these numbers cannot show.

**Trick questions are left out.** The authors wrote 446 questions that the
conversation does not answer. The correct reply is to say so. Each one still
records a message that makes a wrong answer look plausible. Returning that
message is neither right nor wrong for a search engine, so those questions are
scored on their own and kept out of the table. Their numbers are in the saved
results.

**Pictures are not included.** 1,226 messages share an image and carry a written
caption. The authors' code looks for a field that this release of the data does
not contain, so no captions reach any search engine, theirs or ours. Questions
about images fail for all three engines equally.

**One run per row, one machine.** The rows do not repeat to the last digit. Two
runs of the same setup differed by up to three questions in 1,536. MGCP scored
0.680 and then 0.681 on the facts, and 0.628 and then 0.626 on the raw messages.
The search index is approximate, and the graphics hardware does not produce
identical arithmetic twice. Three questions is larger than two of the gaps in the
results table, which is why those gaps were tested instead of read. BM25 repeats
exactly.

## How to repeat this

The data is not stored in this repository. `locomo10.json` is published under the
Creative Commons BY-NC 4.0 licence by Maharana and co-authors, in "Evaluating
Very Long-Term Conversational Memory of LLM Agents". The licence allows research
use and forbids commercial use. These results can go in a paper or a blog post,
but not in sales material.

```bash
curl -sLO https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

for engine in mgcp dragon bm25; do
  for stored_as in observation dialog; do
    .venv/bin/python -m tests.locomo_benchmark \
      --data-file locomo10.json --retriever $engine --mode $stored_as \
      --record-format locomo \
      --out-json docs/locomo-results/cmp-$engine-$stored_as.json \
      --out-per-question /tmp/pq-$engine-$stored_as.json
  done
done
```

The program builds its own throwaway copy of MGCP, and it refuses to run against
`~/.mgcp`. Adding 2,541 facts from somebody else's conversations to a store of
304 working notes would change every number in the other test set.

The saved results in `docs/locomo-results/` are our measurements. They contain no
LoCoMo text.
