"""Compare MGCP's retrieval stack against LoCoMo's own baselines, on LoCoMo.

WHY THIS EXISTS
    MGCP's own labelled set (tests/benchmark_data/retrieval_queries.yaml) has 34
    queries over a corpus of imperative lessons written by one person. It cannot
    say whether the stack generalises to a corpus somebody else built, in another
    register, at another scale. LoCoMo is an external published benchmark of the
    thing MGCP claims to do — find the right memory in a long history — so it is
    the available check on that claim.

    LoCoMo publishes QA F1, not retrieval recall, so there is no number to sit
    beside ours. Rather than compare against a differently-shaped metric, this
    runs THEIR retriever (`dragon`, as configured in their
    scripts/evaluate_rag_gpts.sh) and a lexical BM25 floor over the SAME records,
    the SAME questions and the SAME gold labels. Every number here is measured by
    this harness, so the comparison does not depend on reproducing their
    generation stack.

WHAT IS MEASURED
    The retrieval layer only. Two things above the vector layer are out of scope,
    and no result here should be reported as "MGCP scores X on LoCoMo" without
    saying so:

      - the community bridge, which appends graph neighbours at score 0.0 and is
        31% of slots in the live trace. It needs a curated relationship graph;
        ingesting LoCoMo produces none, so it cannot fire.
      - the SQLite/graph/telemetry layer, which does not affect which vector wins.

RETRIEVERS
    mgcp    BAAI/bge-base-en-v1.5 via MGCP's own embedding module, with its
            asymmetric query instruction prefix, cosine over a real Qdrant index.
    dragon  facebook/dragon-plus-{query,context}-encoder, CLS pooling,
            L2-normalised, cosine — replicating task_eval/rag_utils.py's
            `get_context_embeddings` dragon branch, which is the retriever their
            published RAG rows use.
    bm25    Okapi BM25 (k1=1.5, b=0.75). The lexical floor. A dense retriever
            that cannot beat keyword matching is not earning its inference cost.

    Every retriever sees byte-identical record text, so this isolates the
    retriever. `--encoding template` additionally wraps records in MGCP's storage
    template and is therefore mgcp-only; it is off by default for that reason.

DATABASES, named after their --rag-mode
    observation  their pre-extracted facts, each paired with the dia_id it came
                 from: 2,541 records. Their best-performing condition, and the
                 shape MGCP natively stores.
    dialog       raw turns, one record per utterance: 5,882 records.

RECORD FORMAT
    --record-format locomo  "(1:56 pm on 8 May, 2023) Caroline said, "...""
                            exactly as rag_utils.get_context_embeddings builds it,
                            including the session timestamp. Default, because the
                            timestamp is load-bearing for the temporal category
                            and omitting it handicaps every retriever equally but
                            pointlessly.
    --record-format plain   "Caroline: ..." — no timestamp. The ablation that
                            shows what the timestamp is worth.

SCORING SET
    1,536 of 1,986 questions: categories 1-4 (multi-hop, temporal, open-domain,
    single-hop), less 4 open-domain items carrying no evidence.

    All 446 category-5 (adversarial) items are excluded from recall and reported
    separately. They carry `adversarial_answer` instead of `answer`, and their
    `evidence` points at the turn that makes a wrong answer look plausible — the
    correct QA behaviour is abstention, so retrieving that turn is neither right
    nor wrong at this layer.

KNOWN LIMITATION
    1,226 turns carry a `blip_caption` for a shared image. Their retrieval code
    checks for `img_file`, which this release does not contain, so captions enter
    no pipeline — theirs or ours. Image-grounded questions are therefore
    unanswerable for every retriever here, equally.

DATA
    Not vendored. locomo10.json is CC BY-NC 4.0 (Maharana et al., "Evaluating
    Very Long-Term Conversational Memory of LLM Agents"), fetched by the operator:

        curl -sLO https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json
        python -m tests.locomo_benchmark --data-file locomo10.json --retriever mgcp

    Non-commercial: results are publishable as research, not as product material.

ISOLATION
    Refuses to run against the operator's live data directory and builds its own
    throwaway instance: 2,541 strangers' facts in a 304-lesson curated corpus
    would change every number in the benchmark this is meant to complement.
"""

from __future__ import annotations

import argparse
import json
import math
import os
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
                        # [fact_text, dia_id] — occasionally more than one dia_id.
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
        f"({ceil['distinct_dia_ids']} dia_ids reachable) — recall cannot exceed this",
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-file", required=True, help="path to locomo10.json (not vendored)")
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
            "can only be called a difference with a paired test — these files are "
            "what makes that possible."
        ),
    )
    args = ap.parse_args()

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
        f"LoCoMo retrieval — retriever={args.retriever} mode={args.mode} "
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
            rows.append(
                {
                    "key": f"{r['sample']}||{r['question']}",
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
