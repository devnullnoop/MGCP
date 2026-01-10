#!/usr/bin/env python3
"""UserPromptSubmit hook that detects task-start phrases and activates mandatory workflows.

Supports LLM-controlled suppression via set_reminder_boundary MCP tool.
When suppressed, this hook exits silently to avoid repetitive reminders.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

# Reminder state file location (shared with MCP tool)
STATE_FILE = Path.home() / ".mgcp" / "reminder_state.json"


def check_suppression() -> tuple[bool, str]:
    """Check if reminders should be suppressed and increment counter.

    Returns:
        Tuple of (should_suppress: bool, reason: str)
    """
    try:
        if not STATE_FILE.exists():
            return False, "No state file"

        with open(STATE_FILE) as f:
            state = json.load(f)

        mode = state.get("mode", "counter")

        # Increment call counter
        state["current_call_count"] = state.get("current_call_count", 0) + 1

        # Save updated counter
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

        if mode == "counter":
            suppress_until = state.get("suppress_until_call", 0)
            current = state["current_call_count"]
            if current < suppress_until:
                remaining = suppress_until - current
                return True, f"Suppressed ({remaining} calls remaining)"
            return False, "Counter expired"

        elif mode == "timer":
            suppress_until = state.get("suppress_until_time", 0)
            now = time.time()
            if now < suppress_until:
                remaining_mins = int((suppress_until - now) / 60)
                return True, f"Suppressed ({remaining_mins} minutes remaining)"
            return False, "Timer expired"

    except (json.JSONDecodeError, IOError, OSError, KeyError):
        pass

    return False, "State check failed"


# Feature development triggers - substantial new work
FEATURE_PATTERNS = [
    # Specific implementation patterns with known nouns
    r"\b(implement|implementing|add|adding|create|creating|build|building)\b.{0,40}\b(feature|function|method|class|component|endpoint|api|system|visualization|dashboard|ui|service|module|handler|controller|model|view|page|screen|form|button|modal|dialog|widget|panel|sidebar|navbar|header|footer)\b",
    # Common domain-specific patterns
    r"\b(implement|implementing|add|adding|create|creating|build|building)\b.{0,40}\b(authentication|authorization|login|logout|signup|registration|validation|verification|notification|email|messaging|payment|checkout|cart|search|filter|sort|pagination|upload|download|export|import|sync|cache|logging|monitoring|analytics|tracking)\b",
    r"\bnew\s+(feature|functionality|component|system|endpoint|api)\b",
    r"\blet'?s\s+(implement|add|create|build|work on)\b",
    r"\b(can you|could you|please|i need you to|help me)\b.{0,20}\b(implement|add|create|build)\b",
    r"\b(set up|setting up|configure|configuring)\b.{0,20}\b(new|the)\b",
    r"\bwork on\b.{0,30}\b(feature|implementation|adding)\b",
    # Broad catch-all for implement/add/build/create with substantive object
    r"\b(implement|add|build|create)\s+\w+\s+\w+",  # "implement user authentication", "add error handling"
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

    # Check if reminders are suppressed (LLM-controlled via set_reminder_boundary)
    suppressed, reason = check_suppression()
    if suppressed:
        # Exit silently - LLM has indicated it doesn't need reminders right now
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

    # FALLBACK: Generic code modification detection
    # If the prompt suggests code changes but didn't match a specific workflow,
    # require explicit workflow selection
    CODE_MODIFICATION_PATTERNS = [
        r"\b(change|modify|update|edit|refactor|rewrite|improve|optimize|enhance|clean up)\b.{0,30}\b(code|file|function|class|method|component)\b",
        r"\b(make|write|add)\b.{0,20}\b(changes?|edits?|modifications?)\b",
        r"\b(the|this|that)\s+(code|function|method|class)\s+(should|needs to|must)\b",
        r"\brename\b.{0,20}\b(function|method|variable|class|file)\b",
        r"\b(move|extract|inline|split)\b.{0,20}\b(function|method|class|code)\b",
        r"\bclean\s*up\b",
        r"\brefactor\b",
        r"\b(simplify|streamline|reorganize)\b",
    ]

    for pattern in CODE_MODIFICATION_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            print("""<user-prompt-submit-hook>
═══════════════════════════════════════════════════════════════════════════════
⚠️  MANDATORY WORKFLOW SELECTION REQUIRED
═══════════════════════════════════════════════════════════════════════════════

This task involves CODE MODIFICATIONS. Before writing ANY code, you MUST:

1. Call `mcp__mgcp__list_workflows()` to see available workflows
2. SELECT a workflow that fits this task:
   - `feature-development` - For new features, enhancements, refactoring
   - `bug-fix` - For fixing broken functionality
   - `secure-code-review` - For security-focused changes

3. If a workflow applies:
   - Call `mcp__mgcp__get_workflow("<workflow_id>")`
   - Create TodoWrite entries for each step
   - At EACH step, call `get_workflow_step(workflow_id, step_id, expand_lessons=true)`
   - READ the linked lessons - this is where OWASP and critical guidance surfaces
   - Complete ALL checklist items before moving to next step

4. If NO workflow applies, you MUST state:
   "No workflow applies because: [specific reason]"
   Example reasons:
   - "This is a single-line typo fix with no behavior change"
   - "This is a documentation-only change"
   - "This is reverting a previous change"

**THIS IS NOT OPTIONAL.** Code changes without workflow selection are prohibited.
The workflow ensures research, planning, testing, and review are not skipped.

═══════════════════════════════════════════════════════════════════════════════
</user-prompt-submit-hook>""")
            sys.exit(0)

    sys.exit(0)

if __name__ == "__main__":
    main()
