"""NLI entailment benchmark for the apology gate — the Attack B experiment.

The similarity-based semantic tier was falsified twice
(docs/scope-semantic-apology-gate.md): embeddings encode topic, and the gate
needs a speech act. NLI cross-encoders are trained on exactly the
voice/commitment distinction similarity lacks. This script measures whether
that holds on the retained acceptance set.

PRE-REGISTRATION (written before any model was scored, 2026-07-30):

- PRIMARY config: hypothesis H1, exactly as written in the scope doc on
  2026-07-29: "The speaker is apologizing for their own mistake."
  Score = P(entailment) from the model's own label mapping.
- Ship bar, unchanged from the design doc: EVERY positive scores above EVERY
  negative (full separation) AND the gap between the lowest positive and the
  highest negative is >= 0.05.
- Other hypotheses (H2-H4) are EXPLORATORY. At most one (model, hypothesis)
  config may be selected on the calibration set; that selection must then
  pass the SAME bar on a blind held-out set written by an agent that never
  saw any scores. Passing calibration but failing held-out = REJECTED.
- Falsification: if no config passes both sets, the NLI tier is not shipped
  and the numbers are recorded in the scope doc next to the similarity ones.

Run::

    .venv/bin/python tests/nli_apology_benchmark.py \
        --models cross-encoder/nli-deberta-v3-small \
        --data tests/benchmark_data/apology_sentences.yaml \
        --out /tmp/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

HYPOTHESES = {
    # H1 is the pre-registered primary — verbatim from the scope doc.
    "H1": "The speaker is apologizing for their own mistake.",
    # Exploratory:
    "H2": "The writer admits that they themselves made a mistake.",
    "H3": "I am admitting that I made an error.",
    "H4": "The speaker acknowledges being at fault for something that went wrong.",
}

GAP_BAR = 0.05


def entailment_scores(model_name: str, premises: list[str], hypothesis: str) -> list[float]:
    """P(entailment) for each premise against one hypothesis.

    The entailment index is resolved from the model's own id2label — label
    order differs between NLI checkpoints (cross-encoder/* uses
    contradiction/entailment/neutral; MoritzLaurer/* uses
    entailment/neutral/contradiction). Resolving by name, not position, is
    load-bearing; a wrong index silently inverts the experiment.
    """
    import torch
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(model_name)
    id2label = {i: lbl.lower() for i, lbl in model.model.config.id2label.items()}
    ent_idx = [i for i, lbl in id2label.items() if "entail" in lbl]
    if len(ent_idx) != 1:
        raise SystemExit(f"{model_name}: cannot resolve entailment label in {id2label}")
    ent = ent_idx[0]

    logits = model.predict(
        [(p, hypothesis) for p in premises],
        apply_softmax=False,
        convert_to_numpy=False,
    )
    probs = torch.nn.functional.softmax(torch.stack(list(logits)), dim=-1)
    return [float(p[ent]) for p in probs]


def evaluate(model_name: str, data: dict, hypotheses: dict) -> dict:
    positives, negatives = data["positives"], data["negatives"]
    out = {}
    for key, hyp in hypotheses.items():
        pos = entailment_scores(model_name, positives, hyp)
        neg = entailment_scores(model_name, negatives, hyp)
        min_pos, max_neg = min(pos), max(neg)
        out[key] = {
            "hypothesis": hyp,
            "min_pos": round(min_pos, 4),
            "max_neg": round(max_neg, 4),
            "gap": round(min_pos - max_neg, 4),
            "full_separation": min_pos > max_neg,
            "passes_bar": (min_pos > max_neg) and (min_pos - max_neg >= GAP_BAR),
            "worst_positives": sorted(
                zip([round(s, 4) for s in pos], positives)
            )[:3],
            "worst_negatives": sorted(
                zip([round(s, 4) for s in neg], negatives), reverse=True
            )[:3],
            "scores": {
                "positives": dict(zip(positives, [round(s, 4) for s in pos])),
                "negatives": dict(zip(negatives, [round(s, 4) for s in neg])),
            },
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--data", default="tests/benchmark_data/apology_sentences.yaml")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypotheses", nargs="*", default=list(HYPOTHESES),
                    help="subset of hypothesis keys to run (default: all)")
    args = ap.parse_args()

    data = yaml.safe_load(Path(args.data).read_text())
    hyps = {k: HYPOTHESES[k] for k in args.hypotheses}

    results = {"data_file": args.data, "gap_bar": GAP_BAR, "models": {}}
    for name in args.models:
        print(f"== {name}", file=sys.stderr)
        results["models"][name] = evaluate(name, data, hyps)
        for k, r in results["models"][name].items():
            print(f"   {k}: min_pos={r['min_pos']} max_neg={r['max_neg']} "
                  f"gap={r['gap']} passes={r['passes_bar']}", file=sys.stderr)

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
