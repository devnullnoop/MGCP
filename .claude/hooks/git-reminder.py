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
            # Inject proof-based gate
            print("""<user-prompt-submit-hook>
STOP. Before any git push/commit/PR, execute these commands and SHOW OUTPUT:

```bash
# 1. What changed?
git diff --name-only HEAD~1

# 2. If ANY .md files changed, run this and SHOW OUTPUT:
git diff HEAD~1 -- "*.md" | head -100
```

PASTE THE OUTPUT ABOVE before running git commands.

If the diff shows terminology changes (e.g., ChromaDB→Qdrant), RUN:
```bash
grep -rn "OLD_TERM" *.md README.md CONTRIBUTING.md CLAUDE.md 2>/dev/null
```

Fix any stale references BEFORE pushing. The git command must appear AFTER the grep output.
</user-prompt-submit-hook>""")
            sys.exit(0)

    # No git keywords, allow through silently
    sys.exit(0)

if __name__ == "__main__":
    main()