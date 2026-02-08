"""Run blind LLM classification against the intent corpus.

Sends each message to Claude with ONLY the routing prompt — no corpus knowledge,
no ground truth labels, clean context per message. This is the honest test.

Usage:
    ANTHROPIC_API_KEY=sk-... python tests/benchmark_data/run_blind_llm.py

Outputs: tests/benchmark_data/llm_blind_classifications.yaml
"""

import os
import sys
import time
from pathlib import Path

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from anthropic import Anthropic

CORPUS_PATH = Path(__file__).parent / "intent_corpus.yaml"
OUTPUT_PATH = Path(__file__).parent / "llm_blind_classifications.yaml"

ROUTING_PROMPT = """\
Classify the user's message into zero or more intents. Only include intents
where the user is clearly performing or requesting that action — not merely
mentioning the topic in passing.

Intents:
- git_operation: User wants to commit, push, merge, deploy, create a PR, or ship code
- catalogue_dependency: User mentions adopting, installing, or choosing a library/package/framework
- catalogue_security: User identifies a security vulnerability, auth weakness, or exploit risk
- catalogue_decision: User announces a technical choice ("went with X over Y", "decided on X")
- catalogue_arch_note: User flags a gotcha, quirk, caveat, or surprising behavior
- catalogue_convention: User states a coding rule, naming convention, or style standard
- task_start: User wants to fix, implement, build, refactor, debug, or set up something

If none apply, return: none

Respond with ONLY the intent label(s), comma-separated. No explanation."""

VALID_INTENTS = {
    "git_operation", "catalogue_dependency", "catalogue_security",
    "catalogue_decision", "catalogue_arch_note", "catalogue_convention",
    "task_start", "none",
}


def classify_one(client: Anthropic, text: str, model: str) -> set[str]:
    """Classify a single message using the API."""
    response = client.messages.create(
        model=model,
        max_tokens=50,
        messages=[{"role": "user", "content": f"{ROUTING_PROMPT}\n\nUser message: \"{text}\""}],
    )
    raw = response.content[0].text.strip().lower()

    # Parse comma-separated intents
    intents = set()
    for part in raw.replace(" ", "").split(","):
        part = part.strip()
        if part in VALID_INTENTS:
            intents.add(part)

    return intents if intents else {"none"}


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    # Use haiku for speed and cost
    model = "claude-haiku-4-5-20251001"

    client = Anthropic(api_key=api_key)

    with open(CORPUS_PATH) as f:
        corpus = yaml.safe_load(f)["messages"]

    print(f"Classifying {len(corpus)} messages with {model}...")
    print(f"Estimated cost: ~$0.01 (55 haiku calls)\n")

    classifications = []
    for i, msg in enumerate(corpus):
        text = msg["text"]
        intents = classify_one(client, text, model)
        classifications.append({"text": text, "intents": sorted(intents)})

        truth = set(msg["intents"])
        match = "OK" if intents == truth else "MISS"
        print(f"  [{i+1:2d}/55] [{match:4s}] \"{text}\" -> {sorted(intents)}")

        # Be nice to the API
        if i % 10 == 9:
            time.sleep(0.5)

    # Write output
    output = {
        "model": model,
        "prompt": ROUTING_PROMPT,
        "classifications": classifications,
    }
    with open(OUTPUT_PATH, "w") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False)

    # Score
    exact = sum(
        1 for c, m in zip(classifications, corpus)
        if set(c["intents"]) == set(m["intents"])
    )
    print(f"\nResults: {exact}/{len(corpus)} exact match")
    print(f"Written to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
