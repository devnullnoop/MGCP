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
            # Inject reminder context
            print("""<user-prompt-submit-hook>
BEFORE executing any git operation (commit, push, PR):
1. Call mcp__mgcp__query_lessons with "git commit workflow"
2. Follow any project-specific git lessons (like attribution rules)
3. **MANDATORY DOC REVIEW** - If ANY .md files or docs changed:
   - grep for stale file paths (e.g., 'docs/' references that should be 'src/mgcp/static/')
   - Verify all counts match reality (tool counts, step counts, etc.)
   - Check all tool/function names against actual source code
   - Run `git diff` on docs and verify EVERY change is accurate
4. Then proceed with the git operation
</user-prompt-submit-hook>""")
            sys.exit(0)

    # No git keywords, allow through silently
    sys.exit(0)

if __name__ == "__main__":
    main()