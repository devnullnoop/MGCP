"""Measure lesson-retrieval quality against the labelled query set, and sweep
the similarity floor.

This is the instrument behind ``QdrantVectorStore.search(min_score=...)``. The
floor is a number that must be justified by a curve, not by preference, so the
curve is committed alongside the code.

Run against a COPY of a real store (never the live one -- Qdrant local mode
allows a single client per path and the MCP server holds it)::

    cp -R ~/.mgcp/qdrant /tmp/mgcp-eval/qdrant
    python -m tests.retrieval_benchmark --qdrant-path /tmp/mgcp-eval/qdrant

Metrics, per threshold:

  P@1        positives whose rank-1 hit is relevant. Empty result counts as a
             miss, so this cannot be gamed by returning nothing.
  P@3        precision of what was actually shown: mean over positives that
             returned anything of |relevant & shown| / |shown|, shown = top 3.
  R@3        positives with at least one relevant lesson in the top 3. This is
             the recall side; a floor that buys P@3 by starving R@3 is a
             regression, not a fix.
  neg-empty  hard negatives correctly returning nothing. The reason a floor
             exists at all.
  noise      mean irrelevant lessons shown per query across the whole set --
             what the agent's context window actually pays.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

QUERY_SET = Path(__file__).parent / "benchmark_data" / "retrieval_queries.yaml"

# The floor only ever filters what Qdrant already returned, and query_lessons
# asks for 5. Capturing the top 5 once lets every candidate floor be evaluated
# offline without re-embedding.
SEARCH_LIMIT = 5
RANK_CUTOFF = 3


@dataclass
class Case:
    id: str
    query: str
    paraphrase: str
    gold: str | None
    relevant: set[str]
    kind: str

    @property
    def is_negative(self) -> bool:
        return self.kind.startswith("negative")

    def text(self, variant: str) -> str:
        return self.paraphrase if variant == "paraphrase" else self.query


def load_cases(path: Path = QUERY_SET) -> list[Case]:
    data = yaml.safe_load(path.read_text())
    cases = [
        Case(
            id=q["id"],
            query=q["query"],
            paraphrase=q["paraphrase"],
            gold=q.get("gold"),
            relevant=set(q.get("relevant") or []),
            kind=q["kind"],
        )
        for q in data["queries"]
    ]
    for c in cases:
        if c.is_negative:
            assert not c.relevant, f"{c.id}: negative case must have no relevant set"
        else:
            assert c.gold in c.relevant, f"{c.id}: gold must be in relevant"
    return cases


def collect_raw(
    qdrant_path: str, cases: list[Case], variant: str = "query"
) -> dict[str, list[tuple[str, float]]]:
    """Top-SEARCH_LIMIT (lesson_id, score) per query, unfiltered."""
    from qdrant_client import QdrantClient

    from mgcp.embedding import embed_query

    client = QdrantClient(path=qdrant_path)
    try:
        raw = {}
        for case in cases:
            points = client.query_points(
                collection_name="lessons",
                query=embed_query(case.text(variant)),
                limit=SEARCH_LIMIT,
                with_payload=["lesson_id"],
            ).points
            raw[case.id] = [
                (p.payload.get("lesson_id", str(p.id)), float(p.score)) for p in points
            ]
        return raw
    finally:
        client.close()


def score(cases: list[Case], raw: dict[str, list[tuple[str, float]]], floor: float) -> dict:
    positives = [c for c in cases if not c.is_negative]
    negatives = [c for c in cases if c.is_negative]

    p1_hits = 0
    p3_num, p3_den = 0.0, 0
    r3_hits = 0
    pos_empty = 0
    noise_total = 0

    for c in positives:
        shown = [lid for lid, s in raw[c.id] if s >= floor]
        top = shown[:RANK_CUTOFF]
        noise_total += sum(1 for lid in top if lid not in c.relevant)
        if not shown:
            pos_empty += 1
            continue
        if shown[0] in c.relevant:
            p1_hits += 1
        p3_num += sum(1 for lid in top if lid in c.relevant) / len(top)
        p3_den += 1
        if any(lid in c.relevant for lid in top):
            r3_hits += 1

    neg_empty = 0
    for c in negatives:
        shown = [lid for lid, s in raw[c.id] if s >= floor][:RANK_CUTOFF]
        noise_total += len(shown)
        if not shown:
            neg_empty += 1

    return {
        "floor": floor,
        "p_at_1": p1_hits / len(positives),
        "p_at_3": (p3_num / p3_den) if p3_den else 0.0,
        "r_at_3": r3_hits / len(positives),
        "positive_empty_rate": pos_empty / len(positives),
        "negative_empty_rate": neg_empty / len(negatives),
        "noise_per_query": noise_total / len(cases),
    }


def sweep(cases, raw, floors) -> list[dict]:
    return [score(cases, raw, f) for f in floors]


def format_table(rows: list[dict]) -> str:
    head = (
        f"{'floor':>6} {'P@1':>6} {'P@3':>6} {'R@3':>6} "
        f"{'pos-empty':>10} {'neg-empty':>10} {'noise/q':>8}"
    )
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(
            f"{r['floor']:>6.2f} {r['p_at_1']:>6.2f} {r['p_at_3']:>6.2f} "
            f"{r['r_at_3']:>6.2f} {r['positive_empty_rate']:>10.2f} "
            f"{r['negative_empty_rate']:>10.2f} {r['noise_per_query']:>8.2f}"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant-path", help="path to a COPY of a qdrant dir")
    ap.add_argument(
        "--variant",
        default="query",
        choices=["query", "paraphrase"],
        help="'query' is the calibration set; 'paraphrase' is held out",
    )
    ap.add_argument("--raw-out", help="write captured raw scores here (JSON)")
    ap.add_argument("--raw-in", help="score a previously captured raw JSON, no embedding")
    args = ap.parse_args()

    cases = load_cases()
    if args.raw_in:
        raw = json.loads(Path(args.raw_in).read_text())
        raw = {k: [(lid, s) for lid, s in v] for k, v in raw.items()}
    else:
        if not args.qdrant_path:
            ap.error("--qdrant-path is required unless --raw-in is given")
        raw = collect_raw(args.qdrant_path, cases, args.variant)
    if args.raw_out:
        Path(args.raw_out).write_text(json.dumps(raw, indent=1))

    floors = [round(0.30 + 0.01 * i, 2) for i in range(41)]
    print(format_table(sweep(cases, raw, floors)))


if __name__ == "__main__":
    main()
