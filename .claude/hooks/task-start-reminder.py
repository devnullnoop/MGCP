#!/usr/bin/env python3
"""UserPromptSubmit hook that detects task-start phrases and activates mandatory workflows."""
import json
import re
import sys

# Feature development triggers - substantial new work
FEATURE_PATTERNS = [
    r"\b(implement|implementing|add|adding|create|creating|build|building)\b.{0,40}\b(feature|function|method|class|component|endpoint|api|system|visualization|dashboard|ui)\b",
    r"\bnew\s+(feature|functionality|component|system|endpoint|api)\b",
    r"\blet'?s\s+(implement|add|create|build|work on)\b",
    r"\b(can you|could you|please|i need you to|help me)\b.{0,20}\b(implement|add|create|build)\b",
    r"\b(set up|setting up|configure|configuring)\b.{0,20}\b(new|the)\b",
    r"\bwork on\b.{0,30}\b(feature|implementation|adding)\b",
]

# Bug fix triggers - fixing existing broken things
BUG_FIX_PATTERNS = [
    r"\b(fix|fixing|debug|debugging)\b.{0,30}\b(bug|issue|error|problem|crash)\b",
    r"\b(not working|broken|failing|crashed)\b",
    r"\bwhy is\b.{0,20}\b(happening|broken|failing|wrong)\b",
    r"\b(investigate|troubleshoot|diagnose)\b.{0,20}\b(issue|error|problem|bug)\b",
    r"\bsomething'?s\s+wrong\b",
]

# Security review triggers - code review with security focus
SECURITY_REVIEW_PATTERNS = [
    r"\b(security|secure)\s+(review|audit|check|scan)\b",
    r"\b(review|audit|check).{0,20}(security|vulnerabilit|owasp)",
    r"\b(pen\s*test|penetration\s*test|security\s*test)\b",
    r"\bcheck\s+(for\s+)?(vulnerabilit|injection|xss|csrf|sql)",
    r"\b(owasp|cve|vulnerabilit)\w*\s+(review|check|audit|scan)\b",
    r"\bis\s+(this|it|the\s+code)\s+secure\b",
    r"\bsecurity\s+of\s+(this|the)\b",
    r"\b(audit|analyze).{0,15}security\b",
]

# Don't trigger workflows on these
IGNORE_PATTERNS = [
    r"\b(commit|push|pull|merge|git)\b",  # Handled by git-reminder
    r"^(what|where|how|why|can you explain|tell me about)\b",  # Questions, not tasks
    r"\b(read|show|display|list|check)\b.{0,10}$",  # Simple queries
]

def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    prompt = hook_input.get("prompt", "").lower().strip()

    # Skip if this is a question or simple query
    for pattern in IGNORE_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            sys.exit(0)

    # Check for feature development patterns
    for pattern in FEATURE_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            print("""<user-prompt-submit-hook>
═══════════════════════════════════════════════════════════════════════════════
WORKFLOW ACTIVATION: feature-development
═══════════════════════════════════════════════════════════════════════════════

This task requires the FEATURE DEVELOPMENT workflow. Execute these steps IN ORDER:

**STEP 0 - ACTIVATE WORKFLOW (do this NOW):**
1. Call `mcp__mgcp__get_workflow("feature-development")` to load the full workflow
2. Call `TodoWrite` to create todos for each of the 6 workflow steps

**THEN EXECUTE EACH STEP:**

For EACH workflow step (Research → Plan → Document → Execute → Test → Review):
1. Mark the step as in_progress in TodoWrite
2. Call `mcp__mgcp__get_workflow_step("feature-development", "<step_id>", expand_lessons=true)`
3. READ the linked lessons - they contain critical guidance
4. For important lessons, call `mcp__mgcp__spider_lessons("<lesson_id>")` to get related knowledge
5. Complete ALL checklist items before moving to the next step
6. Mark the step as completed

**CRITICAL:** Do NOT skip steps. Do NOT combine steps. Each step exists to prevent mistakes.

═══════════════════════════════════════════════════════════════════════════════
</user-prompt-submit-hook>""")
            sys.exit(0)

    # Check for bug fix patterns
    for pattern in BUG_FIX_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            print("""<user-prompt-submit-hook>
═══════════════════════════════════════════════════════════════════════════════
WORKFLOW ACTIVATION: bug-fix
═══════════════════════════════════════════════════════════════════════════════

This task requires the BUG FIX workflow. Execute these steps IN ORDER:

**STEP 0 - ACTIVATE WORKFLOW (do this NOW):**
1. Call `mcp__mgcp__get_workflow("bug-fix")` to load the full workflow
2. Call `TodoWrite` to create todos for each of the 4 workflow steps

**THEN EXECUTE EACH STEP:**

For EACH workflow step (Reproduce → Investigate → Fix → Verify):
1. Mark the step as in_progress in TodoWrite
2. Call `mcp__mgcp__get_workflow_step("bug-fix", "<step_id>", expand_lessons=true)`
3. READ the linked lessons - they contain critical debugging guidance
4. For important lessons, call `mcp__mgcp__spider_lessons("<lesson_id>")` to get related knowledge
5. Complete ALL checklist items before moving to the next step
6. Mark the step as completed

**CRITICAL:** Understand the root cause BEFORE applying a fix. Do NOT guess.

═══════════════════════════════════════════════════════════════════════════════
</user-prompt-submit-hook>""")
            sys.exit(0)

    # Check for security review patterns
    for pattern in SECURITY_REVIEW_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            print("""<user-prompt-submit-hook>
═══════════════════════════════════════════════════════════════════════════════
WORKFLOW ACTIVATION: secure-code-review
═══════════════════════════════════════════════════════════════════════════════

This task requires the SECURE CODE REVIEW workflow (OWASP-based). Execute these steps IN ORDER:

**STEP 0 - ACTIVATE WORKFLOW (do this NOW):**
1. Call `mcp__mgcp__get_workflow("secure-code-review")` to load the full workflow
2. Call `TodoWrite` to create todos for each of the 8 workflow steps

**THEN EXECUTE EACH STEP:**

For EACH workflow step:
  Input Validation → Output Encoding → Authentication → Authorization →
  Cryptography → Data Protection → Error Handling & Logging → File Handling

1. Mark the step as in_progress in TodoWrite
2. Call `mcp__mgcp__get_workflow_step("secure-code-review", "<step_id>", expand_lessons=true)`
3. READ the linked OWASP lessons - they contain specific vulnerability checks
4. For critical lessons, call `mcp__mgcp__spider_lessons("<lesson_id>")` to get related security knowledge
5. Complete ALL checklist items before moving to the next step
6. Document any findings with `mcp__mgcp__add_catalogue_security_note`
7. Mark the step as completed

**CRITICAL:** This is a systematic security audit. Do NOT skip steps. Each step covers different vulnerability classes.

═══════════════════════════════════════════════════════════════════════════════
</user-prompt-submit-hook>""")
            sys.exit(0)

    sys.exit(0)

if __name__ == "__main__":
    main()
