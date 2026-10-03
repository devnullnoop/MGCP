"""Measure whether the community bridge supplies lessons that search missed.

WHAT THE BRIDGE IS
    `query_lessons` runs a search, then adds more lessons from the link graph. It
    searches the community summaries, finds the community that matches the
    question, and appends members of that community which the search did not
    return. Those appended slots are recorded with a score of 0.0, because they
    were not matched by relevance.

WHY MEASURE IT
    In the live store, 2,355 of 7,397 returned slots over nine months arrived
    through the bridge, which is 31.8%. Ledger row E05 found that it ranked its
    candidates by `usage_count` and then wrote `usage_count` on what it appended,
    so it ranked by a number it produced. That was fixed. Whether the appended
    lessons are any use was never measured.

HOW
    Two runs over the 34 labelled queries in benchmark_data/retrieval_queries.yaml,
    which carry a gold lesson and a list of every lesson a human accepted as
    relevant. One run with the bridge on, one with it off. Setting
    BRIDGE_MIN_SCORE above 1.0 switches it off without touching its code, because
    cosine similarity cannot exceed 1.0.

    Each run gets its own copy of lessons.db. The bridge ranks candidates by
    `usage_count` and `query_lessons` writes `usage_count`, so one store cannot
    host both conditions: the first run would change the second.

    The operator's store is copied, never opened. This program reads
    MGCP_LIVE_DATA_DIR or ~/.mgcp to find lessons.db, copies it to a temporary
    directory, and every query runs against the copy.

WHAT IT CANNOT SETTLE
    A bridged lesson that nobody labelled is scored as useless here, and it may
    not be. The labels were pooled from a 238-lesson snapshot in July 2026, and
    the store has grown since. So the count of useful appends is a lower bound.

    26 positive queries is a small sample. This measures a direction, not a rate.

    python -m tests.bridge_benchmark
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

QUERY_SET = Path(__file__).resolve().parent / "benchmark_data" / "retrieval_queries.yaml"
LESSON_ID = re.compile(r"^\*\*([a-z0-9][a-z0-9-]*)\*\*:", re.M)
BRIDGE_MARKER = "**Also relevant**"


def live_data_dir() -> Path:
    """Where the operator's real store is. Read for copying, never opened."""
    for name in ("MGCP_LIVE_DATA_DIR", "MGCP_DATA_DIR"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return Path("~/.mgcp").expanduser()


# The shipped threshold, and the sweep around it. 1.1 is unreachable because
# cosine similarity cannot exceed 1.0, so it is the off condition.
SHIPPED_MIN_SCORE = 0.25
BRIDGE_OFF = 1.1
SWEEP = [0.25, 0.35, 0.45, 0.55, 0.60, 0.65, BRIDGE_OFF]


def condition_label(min_score: float) -> str:
    """File-safe name for one threshold.

    The shipped threshold keeps the name "on" and the unreachable one keeps
    "off", because docs/bridge-measurement.md cites bridge-on.json and
    bridge-off.json by path.
    """
    if min_score >= BRIDGE_OFF:
        return "off"
    if abs(min_score - SHIPPED_MIN_SCORE) < 1e-9:
        return "on"
    return f"min{min_score:.2f}".replace(".", "")


async def run_condition(min_score: float, out_path: Path) -> None:
    """One threshold, in a process whose MGCP_DATA_DIR is already a copy."""
    import yaml

    import mgcp.server as srv

    srv.BRIDGE_MIN_SCORE = min_score
    await srv._ensure_initialized()

    cases = yaml.safe_load(QUERY_SET.read_text())["queries"]
    rows = []
    for case in cases:
        output = await srv.query_lessons(case["query"], limit=5)
        head, _, tail = output.partition(BRIDGE_MARKER)
        rows.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "gold": case.get("gold"),
                "relevant": case.get("relevant") or [],
                "direct": LESSON_ID.findall(head),
                "bridged": LESSON_ID.findall(tail) if tail else [],
            }
        )
    out_path.write_text(
        json.dumps(
            {"condition": condition_label(min_score),
             "min_score": min_score,
             "rows": rows},
            indent=1,
        )
    )
    print(
        f"  {condition_label(min_score):>8}: {len(rows)} queries, "
        f"{sum(1 for r in rows if r['bridged'])} with appends, "
        f"{sum(len(r['bridged']) for r in rows)} appended slots"
    )


def score_one(rows: list[dict], baseline: dict[str, list[str]] | None = None) -> dict:
    """Metrics for a single threshold.

    `baseline` is the off run's searched results per query id. The bridge only
    appends, so every threshold must leave them identical. A difference means
    something other than the bridge moved and that row cannot be read.
    """
    by_id = {r["id"]: r for r in rows}
    positives = [r for r in rows if r["kind"] == "positive"]
    negatives = [r for r in rows if r["kind"] != "positive"]

    useful = []
    for r in positives:
        if not r["bridged"]:
            continue
        labelled = set(r["relevant"]) | ({r["gold"]} if r["gold"] else set())
        added = [b for b in r["bridged"] if b in labelled and b not in r["direct"]]
        if added:
            useful.append({"query": r["id"], "added": added})

    slots = sum(len(r["bridged"]) for r in rows)
    return {
        "appended_slots": slots,
        "positives_with_appends": sum(1 for r in positives if r["bridged"]),
        "useful_appends": len(useful),
        "useful_detail": useful,
        # Every append that no label vouches for. This is what the threshold
        # buys back, and it is the cost side of the trade.
        "unvouched_appends": slots - sum(len(u["added"]) for u in useful),
        "appends_reaching_top_3": [
            r["id"] for r in positives
            if any(b in (r["direct"] + r["bridged"])[:3] for b in r["bridged"])
        ],
        "negatives_with_appends": [r["id"] for r in negatives if r["bridged"]],
        "searched_results_differ": (
            sorted(i for i, d in (baseline or {}).items()
                   if i in by_id and by_id[i]["direct"] != d)
        ),
    }


def analyse_sweep(paths: dict[str, Path]) -> dict:
    """One row per threshold, all validated against the off run."""
    loaded = {label: json.loads(p.read_text()) for label, p in paths.items()}
    baseline = {r["id"]: r["direct"] for r in loaded["off"]["rows"]}

    rows = []
    for label, body in sorted(loaded.items(), key=lambda kv: kv[1]["min_score"]):
        row = score_one(body["rows"], baseline)
        row["condition"] = label
        row["min_score"] = body["min_score"]
        rows.append(row)
    return {"baseline_queries": len(baseline), "thresholds": rows}


def format_sweep(result: dict) -> str:
    lines = [
        "\nCommunity bridge: appended slots against useful appends, by threshold",
        "=" * 78,
        "  BRIDGE_MIN_SCORE is the floor a community member must score to be",
        "  appended. Lower means more appends. The question is whether the extra",
        "  appends carry anything a label vouches for.",
        "",
        f"  {'min_score':>9} {'appends':>8} {'useful':>7} {'unvouched':>10} "
        f"{'queries_hit':>12} {'top3':>5} {'negatives':>10}",
        f"  {'-'*9} {'-'*8} {'-'*7} {'-'*10} {'-'*12} {'-'*5} {'-'*10}",
    ]
    void = []
    for r in result["thresholds"]:
        if r["searched_results_differ"]:
            void.append(r["condition"])
        lines.append(
            f"  {r['min_score']:>9.2f} {r['appended_slots']:>8} {r['useful_appends']:>7} "
            f"{r['unvouched_appends']:>10} {r['positives_with_appends']:>12} "
            f"{len(r['appends_reaching_top_3']):>5} {len(r['negatives_with_appends']):>10}"
        )
    lines.append("")
    if void:
        lines.append(
            f"  VOID for {void}: the searched results moved, so the bridge is not "
            "the only thing that changed and those rows cannot be read."
        )
    else:
        lines.append(
            "  Validity: the searched results are identical at every threshold, so "
            "the\n  only difference between these rows is the bridge."
        )
    lines += [
        "",
        "  An append no label vouches for is counted as unvouched, not as useless:",
        "  the labels were pooled from a 238-lesson snapshot and the store has grown.",
        "  26 positive queries is a small sample, so this shows a direction.",
    ]
    return "\n".join(lines)


def analyse(on_path: Path, off_path: Path) -> dict:
    on = {r["id"]: r for r in json.loads(on_path.read_text())["rows"]}
    off = {r["id"]: r for r in json.loads(off_path.read_text())["rows"]}

    # The bridge only appends, so the searched results must be identical. If they
    # are not, something other than the bridge changed and the run is void.
    differing = sorted(i for i in on if on[i]["direct"] != off[i]["direct"])

    positives = [r for r in on.values() if r["kind"] == "positive"]
    with_appends = [r for r in positives if r["bridged"]]
    useful = []
    for r in with_appends:
        labelled = set(r["relevant"]) | ({r["gold"]} if r["gold"] else set())
        added = [b for b in r["bridged"] if b in labelled and b not in r["direct"]]
        if added:
            useful.append({"query": r["id"], "added": added})

    reached_top3 = [
        r["id"]
        for r in positives
        if any(b in (r["direct"] + r["bridged"])[:3] for b in r["bridged"])
    ]
    negatives = [r for r in on.values() if r["kind"] != "positive"]
    return {
        "queries": len(on),
        "positives": len(positives),
        "direct_results_differ": differing,
        "positives_with_appends": len(with_appends),
        "appended_slots": sum(len(r["bridged"]) for r in on.values()),
        "appends_that_added_a_labelled_lesson": len(useful),
        "useful_detail": useful,
        "appends_reaching_top_3": reached_top3,
        "negatives_with_appends": [r["id"] for r in negatives if r["bridged"]],
    }


def format_report(result: dict) -> str:
    lines = [
        "\nCommunity bridge, measured on the labelled query set",
        "=" * 70,
    ]
    if result["direct_results_differ"]:
        lines.append(
            "  VOID: the searched results differ between runs for "
            f"{result['direct_results_differ']}. Only the bridge should have "
            "changed, so this comparison cannot be read."
        )
        return "\n".join(lines)
    lines += [
        "  Validity: the searched results are identical in both runs.",
        "",
        f"  {result['positives_with_appends']} of {result['positives']} positive queries "
        f"received appends, {result['appended_slots']} slots in total.",
        f"  Appends that supplied a labelled-relevant lesson the search missed: "
        f"{result['appends_that_added_a_labelled_lesson']}",
    ]
    for item in result["useful_detail"]:
        lines.append(f"    {item['query']}: {item['added']}")
    lines += [
        f"  Queries where an appended lesson reached the top 3: "
        f"{len(result['appends_reaching_top_3'])}",
        f"  Hard negatives that received appends: "
        f"{len(result['negatives_with_appends'])}",
        "",
        "  A bridged lesson nobody labelled counts as useless here, so the count",
        "  of useful appends is a lower bound. 26 positive queries is a small",
        "  sample, so this shows a direction and not a rate.",
    ]
    return "\n".join(lines)


def _child_run(min_score: float, source: Path, out_path: Path) -> None:
    """Run one threshold in its own process against its own copy of the store.

    A fresh copy per threshold is not tidiness. The bridge ranks candidates by
    `usage_count` and `query_lessons` writes `usage_count`, so a second run
    against the same file would be reading what the first run wrote.
    """
    label = condition_label(min_score)
    with tempfile.TemporaryDirectory(prefix=f"mgcp-bridge-{label}-") as tmp:
        shutil.copy(source, Path(tmp) / "lessons.db")
        env = {**os.environ, "MGCP_DATA_DIR": tmp}
        env.pop("MGCP_QDRANT_URL", None)
        result = subprocess.run(
            [sys.executable, "-m", "tests.bridge_benchmark",
             "--min-score", str(min_score), "--out", str(out_path)],
            env=env, capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith("  "):
                print(line)
        if result.returncode != 0:
            sys.exit(f"the {label} run failed:\n{result.stderr[-2000:]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-score", type=float, help="internal, one child run")
    ap.add_argument("--out", help="internal, where the child writes its rows")
    ap.add_argument("--results-dir", default="docs/bridge-results", help="where to save")
    ap.add_argument(
        "--sweep", action="store_true",
        help="run every threshold in SWEEP, not just the shipped one and off",
    )
    args = ap.parse_args()

    if args.min_score is not None:
        asyncio.run(run_condition(args.min_score, Path(args.out)))
        return

    source = live_data_dir() / "lessons.db"
    if not source.exists():
        sys.exit(f"no lessons.db at {source}. This measures a real store.")

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    print(f"Measuring against a copy of {source}")

    thresholds = SWEEP if args.sweep else [SHIPPED_MIN_SCORE, BRIDGE_OFF]
    paths = {}
    for min_score in thresholds:
        label = condition_label(min_score)
        out = results_dir / f"bridge-{label}.json"
        _child_run(min_score, source, out)
        paths[label] = out

    if args.sweep:
        analysis = analyse_sweep(paths)
        print(format_sweep(analysis))
        dest = results_dir / "bridge-sweep.json"
    else:
        analysis = analyse(paths["on"], paths["off"])
        print(format_report(analysis))
        dest = results_dir / "bridge-analysis.json"

    dest.write_text(json.dumps(analysis, indent=2))
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
