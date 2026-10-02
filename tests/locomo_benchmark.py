"""Compare MGCP's search against the search engines used in the LoCoMo paper.

WHY THIS EXISTS
    MGCP's own test set (tests/benchmark_data/retrieval_queries.yaml) holds 34
    questions about notes written by the same person who wrote the questions. It
    cannot show whether search works on material from somewhere else. LoCoMo is a
    public test set of ten long conversations with 1,986 annotated questions, so
    it is the available check.

    The LoCoMo paper reports how often a language model answers correctly after
    reading search results. This program measures how often search returns the
    right message. Those are two different measurements, so rather than quote
    their number, this runs their search engine here: same stored items, same
    questions, same scoring.

WHAT IT MEASURES
    Search only. Two parts of MGCP are left out, and no result from this program
    should be reported as "MGCP scores X on LoCoMo" without saying so:

      - the link graph, which adds related notes at score 0.0 and supplies 31% of
        results in daily use. It needs links between notes, and importing LoCoMo
        creates none, so it never runs.
      - the SQLite store, the graph, and usage logging, none of which change
        which item search returns first.

SEARCH ENGINES
    mgcp    BAAI/bge-base-en-v1.5 through MGCP's own embedding code, including
            its question prefix, compared by cosine similarity in a real Qdrant
            index.
    dragon  facebook/dragon-plus-query-encoder and -context-encoder, first output
            vector, scaled to length one, compared by cosine similarity. This
            copies the dragon branch of task_eval/rag_utils.py, which is the
            engine behind the paper's published results.
    bm25    Keyword search, Okapi BM25 with k1=1.5 and b=0.75. Any search engine
            that cannot beat keyword matching is not worth its cost.

    All three read the same stored text, character for character, so the result
    reflects the engine and not the text. The --encoding template option also
    wraps each item in MGCP's note format, so it applies to mgcp only and is off
    by default.

HOW ITEMS ARE STORED, using the paper's own names
    observation  the facts the LoCoMo authors extracted, each recording the
                 messages it came from: 2,541 items. This is their
                 best-performing setup, and it matches how MGCP stores notes.
    dialog       raw messages, one item each: 5,882 items.

ITEM FORMAT
    --record-format locomo  '(1:56 pm on 8 May, 2023) Caroline said, "..."'
                            This is what rag_utils.get_context_embeddings builds,
                            including the session timestamp. It is the default,
                            because questions about dates need that timestamp and
                            leaving it out lowers every engine's score for no
                            reason.
    --record-format plain   "Caroline: ..." with no timestamp, which shows what
                            the timestamp is worth.

WHICH QUESTIONS ARE SCORED
    1,536 of 1,986. These are the four kinds that the conversation answers, less
    four questions that record no answer location.

    The other 446 are trick questions, scored on their own. They record an answer
    that the conversation does not support, and the correct reply is to say so.
    Each one still names a message that makes the wrong answer look plausible.
    Returning that message is neither right nor wrong for a search engine.

KNOWN LIMIT
    1,226 messages share an image and carry a written caption. The authors' code
    looks for a field that this release of the data does not contain, so captions
    reach no search engine, theirs or ours. Questions about images fail for all
    three engines equally.

THE DATA
    Not stored in this repository. locomo10.json is licensed CC BY-NC 4.0
    (Maharana and co-authors, "Evaluating Very Long-Term Conversational Memory of
    LLM Agents"), so the operator downloads it:

        curl -sLO https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json
        python -m tests.locomo_benchmark --data-file locomo10.json --retriever mgcp

    Non-commercial. Results may be used in research, not in sales material.

ISOLATION
    This program builds its own throwaway copy of MGCP and refuses to run against
    the operator's data directory. Adding 2,541 facts from somebody else's
    conversations to a store of 304 working notes would change every number in
    the other test set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}
ANSWERABLE = (1, 2, 3, 4)
# The k values LoCoMo's own RAG sweep uses, so columns line up with theirs.
K_VALUES = (5, 10, 25, 50)
SEARCH_LIMIT = max(K_VALUES)
COLLECTION = "locomo_eval"


# --------------------------------------------------------------------------- setup


def _isolate_data_dir(explicit: str | None) -> Path:
    """Point every MGCP store at a throwaway directory, before mgcp is imported.

    DEFAULT_* paths bind at import time, which is why conftest.py does this at
    module scope rather than in a fixture.
    """
    live = Path("~/.mgcp").expanduser().resolve()
    data_dir = (
        Path(explicit).expanduser()
        if explicit
        else Path(tempfile.mkdtemp(prefix="mgcp-locomo-"))
    )
    if data_dir.resolve() == live:
        sys.exit(
            f"refusing to run against the live data directory ({live}). This ingests "
            "thousands of records from someone else's conversations; it belongs in its "
            "own instance. Pass --data-dir, or omit it for a temp directory."
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MGCP_DATA_DIR"] = str(data_dir)
    # An eval instance has no business reaching a server configured for real work.
    os.environ.pop("MGCP_QDRANT_URL", None)
    return data_dir


def slug(text: str) -> str:
    """A record id MGCP would accept: lowercase, dashes, no colons."""
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out or "x"


# ----------------------------------------------------------------------- the corpus


def _format_record(speaker: str, text: str, when: str | None, record_format: str) -> str:
    if record_format == "locomo":
        # rag_utils.get_context_embeddings: '(' + date_time + ') ' + speaker +
        # ' said, "' + text + '"\n'
        stamp = f"({when}) " if when else ""
        return f'{stamp}{speaker} said, "{text}"'
    return f"{speaker}: {text}"


def load_records(
    data: list[dict], mode: str, record_format: str
) -> list[tuple[str, str, set[str]]]:
    """(record_id, text, source dia_ids) per memory record, across all conversations."""
    records: list[tuple[str, str, set[str]]] = []
    for conv in data:
        sample = conv.get("sample_id") or "conv"
        conversation = conv.get("conversation") or {}
        if mode == "observation":
            for session_key, per_speaker in (conv.get("observation") or {}).items():
                base = session_key.replace("_observation", "")
                when = conversation.get(f"{base}_date_time")
                for speaker, facts in per_speaker.items():
                    for i, entry in enumerate(facts):
                        # [fact_text, dia_id]. Sometimes more than one dia_id.
                        text = entry[0] if isinstance(entry, list) else str(entry)
                        ids = (
                            {str(x) for x in entry[1:] if isinstance(x, str)}
                            if isinstance(entry, list)
                            else set()
                        )
                        rid = slug(f"{sample}-{session_key}-{speaker}-{i}")
                        records.append(
                            (rid, _format_record(speaker, text, when, record_format), ids)
                        )
        else:
            for key, turns in conversation.items():
                if not key.startswith("session_") or key.endswith("date_time"):
                    continue
                if not isinstance(turns, list):
                    continue
                when = conversation.get(f"{key}_date_time")
                for turn in turns:
                    dia_id = turn.get("dia_id")
                    if not dia_id:
                        continue
                    records.append(
                        (
                            slug(f"{sample}-{dia_id}"),
                            _format_record(
                                turn.get("speaker", ""),
                                turn.get("text", ""),
                                when,
                                record_format,
                            ),
                            {str(dia_id)},
                        )
                    )
    return records


def load_questions(data: list[dict]) -> list[dict]:
    """Questions with their gold evidence, scoped per conversation."""
    out = []
    for conv in data:
        sample = conv.get("sample_id") or "conv"
        for qa in conv.get("qa") or []:
            out.append(
                {
                    "sample": sample,
                    "question": qa["question"],
                    "category": qa.get("category"),
                    "evidence": {str(e) for e in (qa.get("evidence") or [])},
                }
            )
    return out


def apply_encoding(text: str, encoding: str) -> str:
    """MGCP's storage template, when asked for. mgcp-only by construction."""
    if encoding == "bare":
        return text
    # _lesson_to_text builds "Trigger: ...\nAction: ...". A LoCoMo record is
    # declarative and has no action; this is the least dishonest mapping.
    return f"Trigger: recalling what was said in this conversation\nAction: {text}"


# ------------------------------------------------------------------------ retrievers


def retrieve_mgcp(records, questions, encoding: str) -> list[dict]:
    """MGCP's real stack: its embedding module, its query prefix, a Qdrant index."""
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from mgcp.embedding import EMBEDDING_DIMENSION, embed_batch, embed_query
    from mgcp.qdrant_vector_store import (
        QdrantVectorStore,
        get_default_qdrant_path,
        string_to_uuid,
    )

    store = QdrantVectorStore(persist_path=get_default_qdrant_path())
    client = store.client
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE),
    )

    provenance: dict[str, set[str]] = {}
    batch = 256
    for start in range(0, len(records), batch):
        chunk = records[start : start + batch]
        vectors = embed_batch([apply_encoding(text, encoding) for _r, text, _i in chunk])
        points = []
        for (rid, _text, ids), vector in zip(chunk, vectors):
            provenance[rid] = ids
            points.append(
                PointStruct(id=string_to_uuid(rid), vector=vector, payload={"record_id": rid})
            )
        client.upsert(collection_name=COLLECTION, points=points)
        print(f"  indexed {min(start + batch, len(records))}/{len(records)}", flush=True)

    results = []
    for i, q in enumerate(questions, 1):
        hits = client.query_points(
            collection_name=COLLECTION,
            query=embed_query(q["question"]),
            limit=SEARCH_LIMIT,
            with_payload=True,
        ).points
        results.append(
            {
                **q,
                "ranked": [
                    (
                        h.payload.get("record_id"),
                        float(h.score),
                        provenance.get(h.payload.get("record_id"), set()),
                    )
                    for h in hits
                ],
            }
        )
        if i % 400 == 0:
            print(f"  queried {i}/{len(questions)}", flush=True)
    return results


def retrieve_dragon(records, questions) -> list[dict]:
    """facebook/dragon-plus, exactly as rag_utils.get_context_embeddings does it.

    CLS pooling on last_hidden_state, L2-normalised, cosine. Their other encode
    helper leaves the normalise line commented out; the one that builds the
    context database for their published RAG rows does normalise, so that is the
    behaviour replicated here.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    # Their code uses the QUERY encoder's tokenizer for contexts too.
    tokenizer = AutoTokenizer.from_pretrained("facebook/dragon-plus-query-encoder")
    ctx_model = AutoModel.from_pretrained("facebook/dragon-plus-context-encoder").to(device).eval()
    q_model = AutoModel.from_pretrained("facebook/dragon-plus-query-encoder").to(device).eval()

    def encode(texts: list[str], model, batch: int = 64) -> torch.Tensor:
        out = []
        for start in range(0, len(texts), batch):
            chunk = texts[start : start + batch]
            inputs = tokenizer(
                chunk, padding=True, truncation=True, return_tensors="pt"
            ).to(device)
            with torch.no_grad():
                emb = model(**inputs).last_hidden_state[:, 0, :]
            out.append(torch.nn.functional.normalize(emb, dim=-1).cpu())
            done = min(start + batch, len(texts))
            if done % 1024 < batch:
                print(f"  dragon encoded {done}/{len(texts)}", flush=True)
        return torch.cat(out, dim=0)

    ctx = encode([text for _r, text, _i in records], ctx_model)
    qry = encode([q["question"] for q in questions], q_model)

    ids = [rid for rid, _t, _i in records]
    provenance = {rid: i for rid, _t, i in records}
    scores = qry @ ctx.T
    top = scores.topk(min(SEARCH_LIMIT, len(ids)), dim=-1)
    results = []
    for row, q in enumerate(questions):
        ranked = [
            (ids[int(j)], float(top.values[row][c]), provenance[ids[int(j)]])
            for c, j in enumerate(top.indices[row])
        ]
        results.append({**q, "ranked": ranked})
    return results


def retrieve_bm25(records, questions) -> list[dict]:
    """Okapi BM25, k1=1.5 b=0.75. The lexical floor every dense claim needs."""
    k1, b = 1.5, 0.75
    token = re.compile(r"[a-z0-9']+")

    def tok(text: str) -> list[str]:
        return token.findall(text.lower())

    docs = [tok(text) for _r, text, _i in records]
    ids = [rid for rid, _t, _i in records]
    provenance = {rid: i for rid, _t, i in records}
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / max(1, n)
    tf = [Counter(d) for d in docs]
    df: Counter[str] = Counter()
    for counts in tf:
        df.update(counts.keys())
    idf = {
        term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
    }
    postings: dict[str, list[int]] = defaultdict(list)
    for i, counts in enumerate(tf):
        for term in counts:
            postings[term].append(i)

    results = []
    for qi, q in enumerate(questions, 1):
        scores: dict[int, float] = defaultdict(float)
        for term in tok(q["question"]):
            if term not in idf:
                continue
            weight = idf[term]
            for i in postings[term]:
                freq = tf[i][term]
                dl = len(docs[i])
                scores[i] += weight * (freq * (k1 + 1)) / (
                    freq + k1 * (1 - b + b * dl / avgdl)
                )
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:SEARCH_LIMIT]
        results.append(
            {**q, "ranked": [(ids[i], float(s), provenance[ids[i]]) for i, s in top]}
        )
        if qi % 500 == 0:
            print(f"  bm25 scored {qi}/{len(questions)}", flush=True)
    return results


# --------------------------------------------------------------------------- scoring


def score(results: list[dict]) -> dict:
    """recall@k, all-evidence@k and MRR@10, overall and per category."""
    buckets: dict[object, list[dict]] = defaultdict(list)
    for r in results:
        if r["category"] in ANSWERABLE and r["evidence"]:
            buckets[r["category"]].append(r)
            buckets["all"].append(r)
        elif r["category"] == 5:
            buckets["adversarial"].append(r)

    out = {}
    for key, rows in buckets.items():
        if not rows:
            continue
        stats: dict = {"n": len(rows)}
        for k in K_VALUES:
            any_hit, full_hit = 0, 0
            for r in rows:
                shown: set[str] = set()
                for _rid, _s, ids in r["ranked"][:k]:
                    shown |= ids
                if shown & r["evidence"]:
                    any_hit += 1
                if r["evidence"] <= shown:
                    full_hit += 1
            stats[f"recall@{k}"] = any_hit / len(rows)
            stats[f"all-ev@{k}"] = full_hit / len(rows)
        rr = []
        for r in rows:
            rank = next(
                (
                    i
                    for i, (_rid, _s, ids) in enumerate(r["ranked"][:10], 1)
                    if ids & r["evidence"]
                ),
                None,
            )
            rr.append(1 / rank if rank else 0.0)
        stats["MRR@10"] = statistics.mean(rr)
        stats["top1_score"] = statistics.median(
            [r["ranked"][0][1] for r in rows if r["ranked"]] or [0.0]
        )
        out[key] = stats
    return out


def ceiling(records, questions) -> dict:
    """The best recall achievable, which is not 1.0.

    In observation mode a fact carries only the dia_ids the extractor recorded,
    so some gold evidence is unreachable however good retrieval is. Reporting
    recall without this invites reading 0.80 as 80% of what was possible.
    """
    reachable: set[str] = set()
    for _rid, _text, ids in records:
        reachable |= ids
    answerable = [q for q in questions if q["category"] in ANSWERABLE and q["evidence"]]
    return {
        "distinct_dia_ids": len(reachable),
        "any": sum(1 for q in answerable if q["evidence"] & reachable) / len(answerable),
        "all": sum(1 for q in answerable if q["evidence"] <= reachable) / len(answerable),
    }


def floor_sweep(results, floors=(0.30, 0.40, 0.50, 0.55, 0.60, 0.65)) -> list[dict]:
    """What a similarity floor would do here. Only meaningful for cosine scores."""
    answerable = [r for r in results if r["category"] in ANSWERABLE and r["evidence"]]
    rows = []
    for floor in floors:
        empty, hit5 = 0, 0
        for r in answerable:
            kept = [(rid, s, ids) for rid, s, ids in r["ranked"] if s >= floor]
            if not kept:
                empty += 1
                continue
            shown: set[str] = set()
            for _rid, _s, ids in kept[:5]:
                shown |= ids
            if shown & r["evidence"]:
                hit5 += 1
        rows.append(
            {
                "floor": floor,
                "recall@5": hit5 / len(answerable),
                "returned-nothing": empty / len(answerable),
            }
        )
    return rows


def format_report(label, n_records, scored, sweep, ceil, cosine: bool) -> str:
    lines = [
        f"\n{label}  records={n_records}",
        "=" * 78,
        f"{'set':<14}{'n':>6}{'R@5':>8}{'R@10':>8}{'R@25':>8}{'R@50':>8}{'MRR@10':>9}{'med top1':>10}",
        "-" * 78,
    ]
    order = ["all"] + [c for c in ANSWERABLE if c in scored] + ["adversarial"]
    for key in order:
        if key not in scored:
            continue
        s = scored[key]
        name = (
            "ALL answerable"
            if key == "all"
            else ("adversarial" if key == "adversarial" else f"{key}:{CATEGORY_NAMES[key]}")
        )
        lines.append(
            f"{name:<14}{s['n']:>6}{s.get('recall@5', 0):>8.3f}{s.get('recall@10', 0):>8.3f}"
            f"{s.get('recall@25', 0):>8.3f}{s.get('recall@50', 0):>8.3f}"
            f"{s.get('MRR@10', 0):>9.3f}{s.get('top1_score', 0):>10.3f}"
        )
    lines += [
        "",
        f"ceiling: any-evidence {ceil['any']:.3f}, all-evidence {ceil['all']:.3f} "
        f"({ceil['distinct_dia_ids']} dia_ids reachable). No score can exceed this.",
        f"  R@50 as a fraction of ceiling: {scored['all']['recall@50'] / ceil['any']:.3f}",
        "",
        "all-evidence@k (every gold turn retrieved, not just one):",
        f"  @5 {scored['all']['all-ev@5']:.3f}   @10 {scored['all']['all-ev@10']:.3f}"
        f"   @25 {scored['all']['all-ev@25']:.3f}   @50 {scored['all']['all-ev@50']:.3f}",
    ]
    if cosine:
        lines += [
            "",
            "similarity floor (MGCP ships 0.30, calibrated on imperative lessons):",
            f"  {'floor':>7}{'recall@5':>11}{'returned nothing':>19}",
        ]
        for row in sweep:
            lines.append(
                f"  {row['floor']:>7.2f}{row['recall@5']:>11.3f}{row['returned-nothing']:>19.3f}"
            )
    return "\n".join(lines)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact test on the questions where the two engines disagree.

    Both engines answer the same questions, so the samples are paired. Comparing
    two rates without accounting for that can call a one-question difference a
    result. Only the disagreements carry information: b is the count where the
    first engine wins, c where the second does.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def bootstrap_ci(pairs, iterations: int = 10000, seed: int = 7):
    """A 95% interval for the difference, by resampling the question list."""
    rng = random.Random(seed)
    n = len(pairs)
    diffs = []
    for _ in range(iterations):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        diffs.append(sum(a for a, _ in sample) / n - sum(b for _, b in sample) / n)
    diffs.sort()
    return diffs[int(0.025 * iterations)], diffs[int(0.975 * iterations)]


def compare(paths: list[str], k: int = 5) -> dict:
    """Compare per-question files pairwise, with a paired test on each pair."""
    runs = {}
    for path in paths:
        rows = json.loads(Path(path).read_text())
        label = Path(path).stem.replace("pq-", "")
        runs[label] = {row["key"]: row for row in rows}

    labels = list(runs)
    shared = set.intersection(*[set(r) for r in runs.values()])
    keys = sorted(shared)
    out = {"k": k, "questions": len(keys), "runs": {}, "pairs": []}
    for label in labels:
        hits = [runs[label][key][f"hit@{k}"] for key in keys]
        out["runs"][label] = round(sum(hits) / len(hits), 4)

    for i, first in enumerate(labels):
        for second in labels[i + 1 :]:
            a = [bool(runs[first][key][f"hit@{k}"]) for key in keys]
            b = [bool(runs[second][key][f"hit@{k}"]) for key in keys]
            first_only = sum(1 for x, y in zip(a, b) if x and not y)
            second_only = sum(1 for x, y in zip(a, b) if y and not x)
            p = mcnemar_exact(first_only, second_only)
            low, high = bootstrap_ci(list(zip(a, b)))
            out["pairs"].append(
                {
                    "a": first,
                    "b": second,
                    "recall_a": round(sum(a) / len(a), 4),
                    "recall_b": round(sum(b) / len(b), 4),
                    "difference": round(sum(a) / len(a) - sum(b) / len(b), 4),
                    "ci_low": round(low, 4),
                    "ci_high": round(high, 4),
                    "a_only": first_only,
                    "b_only": second_only,
                    "mcnemar_p": round(p, 6),
                    "verdict": "different" if p < 0.05 else "too small to call",
                }
            )
    return out


def format_comparison(result: dict) -> str:
    lines = [
        f"\nPaired comparison on hit@{result['k']}, {result['questions']} questions",
        "=" * 78,
    ]
    for label, recall in sorted(result["runs"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {label:<24}{recall:.3f}")
    lines += ["", f"{'pair':<34}{'diff':>8}{'95% interval':>20}{'p':>10}  verdict", "-" * 78]
    for pair in result["pairs"]:
        name = f"{pair['a']} vs {pair['b']}"
        interval = f"[{pair['ci_low']:+.3f}, {pair['ci_high']:+.3f}]"
        lines.append(
            f"{name:<34}{pair['difference']:>+8.3f}{interval:>20}"
            f"{pair['mcnemar_p']:>10.4g}  {pair['verdict']}"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--compare",
        nargs="+",
        metavar="PER_QUESTION_JSON",
        help=(
            "compare per-question files instead of running a search. Needs no "
            "dataset, because those files hold hashed question keys and outcomes."
        ),
    )
    ap.add_argument("--data-file", help="path to locomo10.json (not vendored)")
    ap.add_argument("--retriever", choices=["mgcp", "dragon", "bm25"], default="mgcp")
    ap.add_argument("--mode", choices=["observation", "dialog"], default="observation")
    ap.add_argument("--record-format", choices=["locomo", "plain"], default="locomo")
    ap.add_argument(
        "--encoding",
        choices=["bare", "template"],
        default="bare",
        help="mgcp only: wrap records in MGCP's storage template",
    )
    ap.add_argument("--data-dir", help="MGCP instance to use (default: a temp dir)")
    ap.add_argument("--conversations", type=int, help="limit for a smoke run")
    ap.add_argument("--out-json", help="write the full scored result here")
    ap.add_argument(
        "--out-per-question",
        help=(
            "write per-question outcomes here. Two retrievers run on the same "
            "questions are a PAIRED sample, so a one-point difference in recall "
            "can only be called a difference with a paired test. These files are "
            "what makes that possible."
        ),
    )
    args = ap.parse_args()

    if args.compare:
        result = compare(args.compare)
        print(format_comparison(result))
        if args.out_json:
            Path(args.out_json).write_text(json.dumps(result, indent=2))
            print(f"\nwrote {args.out_json}")
        return

    if not args.data_file:
        ap.error("--data-file is required unless --compare is given")

    if args.encoding == "template" and args.retriever != "mgcp":
        ap.error("--encoding template is mgcp-only; it would not be a retriever comparison")

    data_dir = _isolate_data_dir(args.data_dir)
    data = json.loads(Path(args.data_file).read_text())
    if args.conversations:
        data = data[: args.conversations]

    records = load_records(data, args.mode, args.record_format)
    questions = load_questions(data)
    answerable = [q for q in questions if q["category"] in ANSWERABLE and q["evidence"]]
    label = (
        f"LoCoMo search: retriever={args.retriever} mode={args.mode} "
        f"format={args.record_format}"
        + (f" encoding={args.encoding}" if args.retriever == "mgcp" else "")
    )
    print(f"isolated MGCP instance: {data_dir}")
    print(
        f"{len(data)} conversations | {len(records)} records ({args.mode}/"
        f"{args.record_format}) | {len(questions)} questions, {len(answerable)} scored"
    )
    print(f"  sample record: {records[0][1][:110]!r}")

    if args.retriever == "mgcp":
        results = retrieve_mgcp(records, questions, args.encoding)
    elif args.retriever == "dragon":
        results = retrieve_dragon(records, questions)
    else:
        results = retrieve_bm25(records, questions)

    scored = score(results)
    sweep = floor_sweep(results)
    ceil = ceiling(records, questions)
    print(format_report(label, len(records), scored, sweep, ceil, cosine=args.retriever != "bm25"))

    if args.out_per_question:
        rows = []
        for r in results:
            if r["category"] not in ANSWERABLE or not r["evidence"]:
                continue
            per_k = {}
            for k in K_VALUES:
                shown: set[str] = set()
                for _rid, _s, ids in r["ranked"][:k]:
                    shown |= ids
                per_k[f"hit@{k}"] = bool(shown & r["evidence"])
            rank = next(
                (
                    i
                    for i, (_rid, _s, ids) in enumerate(r["ranked"][:50], 1)
                    if ids & r["evidence"]
                ),
                None,
            )
            # The question text is LoCoMo's, under CC BY-NC, so it is hashed
            # rather than written out. A hash is stable across runs, which is all
            # the pairing needs, and it lets this file be committed as evidence
            # without copying their data into the repository.
            digest = hashlib.sha256(
                f"{r['sample']}||{r['question']}".encode()
            ).hexdigest()[:16]
            rows.append(
                {
                    "key": digest,
                    "category": r["category"],
                    "rank": rank,
                    **per_k,
                }
            )
        Path(args.out_per_question).write_text(json.dumps(rows))
        print(f"wrote {args.out_per_question} ({len(rows)} questions)")

    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps(
                {
                    "retriever": args.retriever,
                    "mode": args.mode,
                    "record_format": args.record_format,
                    "encoding": args.encoding if args.retriever == "mgcp" else None,
                    "records": len(records),
                    "scored": {str(k): v for k, v in scored.items()},
                    "ceiling": ceil,
                    "floor_sweep": sweep,
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.out_json}")


if __name__ == "__main__":
    main()
