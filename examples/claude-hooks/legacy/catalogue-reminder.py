#!/usr/bin/env python3
"""Hook that detects library/framework/decision mentions and reminds to catalogue them."""

import json
import re
import sys

# Patterns that suggest something should be catalogued
CATALOGUE_PATTERNS = [
    # Libraries and frameworks
    (r"\b(using|chose|picked|selected|went with|decided on|installed|added)\b.{0,30}\b(library|package|framework|tool|dependency)\b", "dependency"),
    (r"\b(pip install|npm install|cargo add|go get)\b", "dependency"),

    # Security concerns
    (r"\b(security|vulnerability|cve|exploit|injection|xss|csrf|auth)\b.{0,20}\b(issue|bug|concern|problem|risk)\b", "security"),
    (r"\b(never|don't|avoid).{0,20}\b(expose|leak|hardcode).{0,15}(secret|key|password|token)\b", "security"),

    # Architectural decisions
    (r"\b(decided|choosing|picked|went with)\b.{0,20}\b(over|instead of|rather than)\b", "decision"),
    (r"\b(architecture|design|pattern|approach)\b.{0,20}\b(decision|choice)\b", "decision"),
    # References to PRIOR decisions (critical - these should be catalogued if not already)
    (r"\b(didn't we|did we not|we determined|we decided|we agreed|as we discussed|remember when we)\b", "prior_decision"),

    # Gotchas and quirks
    (r"\b(gotcha|quirk|caveat|watch out|careful|tricky|bug)\b", "arch_note"),
    (r"\b(doesn't work|won't work|breaks|fails)\b.{0,20}\b(when|if|unless)\b", "arch_note"),

    # Conventions
    (r"\b(convention|naming|style|always|never)\b.{0,20}\b(use|follow|do|avoid)\b", "convention"),

    # File couplings
    (r"\b(these files|both files|coupled|together|in sync)\b", "coupling"),
]

REMINDER_TEMPLATES = {
    "dependency": "REQUIRED: Call mcp__mgcp__search_catalogue with query about this dependency. SHOW OUTPUT. If not found, call add_catalogue_dependency.",
    "security": "REQUIRED: Call mcp__mgcp__add_catalogue_security_note NOW. SHOW OUTPUT. Do not proceed without documenting this.",
    "decision": "REQUIRED: Call mcp__mgcp__add_catalogue_decision NOW. SHOW OUTPUT. Include rationale and alternatives considered.",
    "arch_note": "REQUIRED: Call mcp__mgcp__add_catalogue_arch_note NOW. SHOW OUTPUT. Capture the gotcha/pattern before you forget.",
    "convention": "REQUIRED: Call mcp__mgcp__add_catalogue_convention NOW. SHOW OUTPUT. Document the rule.",
    "coupling": "REQUIRED: Call mcp__mgcp__add_catalogue_coupling NOW. SHOW OUTPUT. List the coupled files.",
    "prior_decision": "STOP. User referenced a prior decision. Call mcp__mgcp__search_catalogue to find it. SHOW OUTPUT. If not found, call add_catalogue_decision NOW before proceeding.",
}

def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    prompt = hook_input.get("prompt", "")

    # Check each pattern
    detected = set()
    for pattern, catalogue_type in CATALOGUE_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            detected.add(catalogue_type)

    if detected:
        reminders = [REMINDER_TEMPLATES[t] for t in detected]
        print(f"<user-prompt-submit-hook>")
        print("Catalogue reminder - you mentioned something that might be worth documenting:")
        for reminder in reminders:
            print(f"  - {reminder}")
        print("</user-prompt-submit-hook>")

    sys.exit(0)

if __name__ == "__main__":
    main()