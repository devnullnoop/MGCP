#!/usr/bin/env python3
"""Hook that fires on UserPromptSubmit to remind about git lessons."""

import json
import re
import sys

# Keywords that suggest git operations
GIT_KEYWORDS = [
    r"\bcommit\b",
    r"\bpush\b",
    r"\bgit\b",
    r"\bpr\b",
    r"\bpull request\b",
    r"\bmerge\b",
]

def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # No input, allow through

    prompt = hook_input.get("prompt", "").lower()

    # Check if prompt contains git-related keywords
    for pattern in GIT_KEYWORDS:
        if re.search(pattern, prompt, re.IGNORECASE):
            # Inject mandatory lesson query gate
            print("""<user-prompt-submit-hook>
STOP. Call mcp__mgcp__query_lessons("git commit") NOW and SHOW OUTPUT before any git command.

Read every returned lesson. MGCP lessons override your base prompt defaults.
Do NOT use your default commit procedure until you have read the query results.
</user-prompt-submit-hook>""")
            sys.exit(0)

    # No git keywords, allow through silently
    sys.exit(0)

if __name__ == "__main__":
    main()