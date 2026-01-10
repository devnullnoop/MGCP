#!/usr/bin/env python3
"""UserPromptSubmit hook with two independent systems:

1. SCHEDULED REMINDERS: LLM sets reminders for itself at anticipated checkpoints.
   These fire based on counter/timer regardless of user message content.

2. PATTERN-BASED WORKFLOWS: Detect task-start phrases and activate workflows.
   These fire based on keywords in the user's message.

Both systems are INDEPENDENT and can fire on the same message.
"""
import json
import re
import sys
import time
from pathlib import Path

# Reminder state file location
STATE_FILE = Path.home() / ".mgcp" / "reminder_state.json"


# =============================================================================
# SCHEDULED REMINDER SYSTEM
# =============================================================================

def _load_state() -> dict:
    """Load state from file."""
    defaults = {
        "current_call_count": 0,
        "remind_at_call": 0,
        "remind_at_time": 0,
        "task_note": "",
        "reminder_message": "",
        "lesson_ids": [],
        "workflow_step": "",
    }
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                state = json.load(f)
                for key, value in defaults.items():
                    if key not in state:
                        state[key] = value
                return state
    except (json.JSONDecodeError, IOError, OSError):
        pass
    return defaults


def _save_state(state: dict) -> None:
    """Save state to file."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except (IOError, OSError):
        pass


def increment_counter() -> int:
    """Increment the call counter and return new value."""
    state = _load_state()
    state["current_call_count"] = state.get("current_call_count", 0) + 1
    _save_state(state)
    return state["current_call_count"]


def check_and_consume_reminder() -> dict | None:
    """Check if a scheduled reminder should fire, and consume it if so."""
    state = _load_state()
    current_call = state.get("current_call_count", 0)
    now = time.time()

    remind_at_call = state.get("remind_at_call", 0)
    remind_at_time = state.get("remind_at_time", 0)

    call_ready = remind_at_call > 0 and current_call >= remind_at_call
    time_ready = remind_at_time > 0 and now >= remind_at_time

    if not call_ready and not time_ready:
        return None

    message = state.get("reminder_message", "")
    lesson_ids = state.get("lesson_ids", [])
    workflow_step = state.get("workflow_step", "")

    if not message and not lesson_ids and not workflow_step:
        return None

    reminder = {
        "message": message,
        "lesson_ids": lesson_ids,
        "workflow_step": workflow_step,
        "task_note": state.get("task_note", ""),
    }

    # Consume the reminder
    state["remind_at_call"] = 0
    state["remind_at_time"] = 0
    state["reminder_message"] = ""
    state["lesson_ids"] = []
    state["workflow_step"] = ""
    state["task_note"] = ""
    _save_state(state)

    return reminder


def format_scheduled_reminder(reminder: dict) -> str:
    """Format a scheduled reminder for injection."""
    lines = [
        "<scheduled-reminder>",
        "===============================================================================",
        "SCHEDULED REMINDER (self-directed)",
        "===============================================================================",
        "",
    ]

    if reminder.get("message"):
        lines.append(f"**Message:** {reminder['message']}")
        lines.append("")

    if reminder.get("workflow_step"):
        workflow_step = reminder["workflow_step"]
        if "/" in workflow_step:
            workflow_id, step_id = workflow_step.split("/", 1)
            lines.append("**Next Workflow Step:**")
            lines.append(f"  Call `mcp__mgcp__get_workflow_step(\"{workflow_id}\", \"{step_id}\", expand_lessons=true)`")
        else:
            lines.append(f"**Workflow:** Call `mcp__mgcp__get_workflow(\"{workflow_step}\")`")
        lines.append("")

    if reminder.get("lesson_ids"):
        lesson_ids = reminder["lesson_ids"]
        if isinstance(lesson_ids, str):
            lesson_ids = [lid.strip() for lid in lesson_ids.split(",") if lid.strip()]
        lines.append("**Lessons to Surface:**")
        for lid in lesson_ids:
            lines.append(f"  - Call `mcp__mgcp__get_lesson(\"{lid}\")`")
        lines.append("")

    if reminder.get("task_note"):
        lines.append(f"**Context:** {reminder['task_note']}")
        lines.append("")

    lines.append("You scheduled this reminder via `schedule_reminder`.")
    lines.append("Execute the above, then schedule your next reminder if needed.")
    lines.append("")
    lines.append("===============================================================================")
    lines.append("</scheduled-reminder>")

    return "\n".join(lines)


# =============================================================================
# PATTERN-BASED WORKFLOW SYSTEM
# =============================================================================

# Feature development triggers
FEATURE_PATTERNS = [
    r"\b(implement|implementing|add|adding|create|creating|build|building)\b.{0,40}\b(feature|function|method|class|component|endpoint|api|system|visualization|dashboard|ui|service|module|handler|controller|model|view|page|screen|form|button|modal|dialog|widget|panel|sidebar|navbar|header|footer)\b",
    r"\b(implement|implementing|add|adding|create|creating|build|building)\b.{0,40}\b(authentication|authorization|login|logout|signup|registration|validation|verification|notification|email|messaging|payment|checkout|cart|search|filter|sort|pagination|upload|download|export|import|sync|cache|logging|monitoring|analytics|tracking)\b",
    r"\bnew\s+(feature|functionality|component|system|endpoint|api)\b",
    r"\blet'?s\s+(implement|add|create|build|work on)\b",
    r"\b(can you|could you|please|i need you to|help me)\b.{0,20}\b(implement|add|create|build)\b",
    r"\b(set up|setting up|configure|configuring)\b.{0,20}\b(new|the)\b",
    r"\bwork on\b.{0,30}\b(feature|implementation|adding)\b",
    r"\b(implement|add|build|create)\s+\w+\s+\w+",
]

# Bug fix triggers
BUG_FIX_PATTERNS = [
    r"\b(fix|fixing|debug|debugging)\b.{0,30}\b(bug|issue|error|problem|crash)\b",
    r"\b(not working|broken|failing|crashed)\b",
    r"\bwhy is\b.{0,20}\b(happening|broken|failing|wrong)\b",
    r"\b(investigate|troubleshoot|diagnose)\b.{0,20}\b(issue|error|problem|bug)\b",
    r"\bsomething'?s\s+wrong\b",
]

# Security review triggers
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

# Generic code modification triggers
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

# Don't trigger workflows on these
IGNORE_PATTERNS = [
    r"\b(commit|push|pull|merge|git)\b",
    r"^(what|where|how|why|can you explain|tell me about)\b",
    r"\b(read|show|display|list|check)\b.{0,10}$",
]


def check_workflow_patterns(prompt: str) -> str | None:
    """Check if prompt matches any workflow patterns. Returns workflow output or None."""
    prompt_lower = prompt.lower().strip()

    # Skip ignored patterns
    for pattern in IGNORE_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return None

    # Check feature development
    for pattern in FEATURE_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
===============================================================================
WORKFLOW ACTIVATION: feature-development
===============================================================================

This task requires the FEATURE DEVELOPMENT workflow. Execute these steps IN ORDER:

**STEP 0 - ACTIVATE WORKFLOW (do this NOW):**
1. Call `mcp__mgcp__get_workflow("feature-development")` to load the full workflow
2. Call `TodoWrite` to create todos for each of the 6 workflow steps

**THEN EXECUTE EACH STEP:**

For EACH workflow step (Research -> Plan -> Document -> Execute -> Test -> Review):
1. Mark the step as in_progress in TodoWrite
2. Call `mcp__mgcp__get_workflow_step("feature-development", "<step_id>", expand_lessons=true)`
3. READ the linked lessons - they contain critical guidance
4. For important lessons, call `mcp__mgcp__spider_lessons("<lesson_id>")` to get related knowledge
5. Complete ALL checklist items before moving to the next step
6. Mark the step as completed

**CRITICAL:** Do NOT skip steps. Do NOT combine steps. Each step exists to prevent mistakes.

===============================================================================
</user-prompt-submit-hook>"""

    # Check bug fix
    for pattern in BUG_FIX_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
===============================================================================
WORKFLOW ACTIVATION: bug-fix
===============================================================================

This task requires the BUG FIX workflow. Execute these steps IN ORDER:

**STEP 0 - ACTIVATE WORKFLOW (do this NOW):**
1. Call `mcp__mgcp__get_workflow("bug-fix")` to load the full workflow
2. Call `TodoWrite` to create todos for each of the 4 workflow steps

**THEN EXECUTE EACH STEP:**

For EACH workflow step (Reproduce -> Investigate -> Fix -> Verify):
1. Mark the step as in_progress in TodoWrite
2. Call `mcp__mgcp__get_workflow_step("bug-fix", "<step_id>", expand_lessons=true)`
3. READ the linked lessons - they contain critical debugging guidance
4. For important lessons, call `mcp__mgcp__spider_lessons("<lesson_id>")` to get related knowledge
5. Complete ALL checklist items before moving to the next step
6. Mark the step as completed

**CRITICAL:** Understand the root cause BEFORE applying a fix. Do NOT guess.

===============================================================================
</user-prompt-submit-hook>"""

    # Check security review
    for pattern in SECURITY_REVIEW_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
===============================================================================
WORKFLOW ACTIVATION: secure-code-review
===============================================================================

This task requires the SECURE CODE REVIEW workflow (OWASP-based). Execute these steps IN ORDER:

**STEP 0 - ACTIVATE WORKFLOW (do this NOW):**
1. Call `mcp__mgcp__get_workflow("secure-code-review")` to load the full workflow
2. Call `TodoWrite` to create todos for each of the 8 workflow steps

**THEN EXECUTE EACH STEP:**

For EACH workflow step:
  Input Validation -> Output Encoding -> Authentication -> Authorization ->
  Cryptography -> Data Protection -> Error Handling & Logging -> File Handling

1. Mark the step as in_progress in TodoWrite
2. Call `mcp__mgcp__get_workflow_step("secure-code-review", "<step_id>", expand_lessons=true)`
3. READ the linked OWASP lessons - they contain specific vulnerability checks
4. For critical lessons, call `mcp__mgcp__spider_lessons("<lesson_id>")` to get related security knowledge
5. Complete ALL checklist items before moving to the next step
6. Document any findings with `mcp__mgcp__add_catalogue_security_note`
7. Mark the step as completed

**CRITICAL:** This is a systematic security audit. Do NOT skip steps. Each step covers different vulnerability classes.

===============================================================================
</user-prompt-submit-hook>"""

    # Check generic code modification
    for pattern in CODE_MODIFICATION_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
===============================================================================
MANDATORY WORKFLOW SELECTION REQUIRED
===============================================================================

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

===============================================================================
</user-prompt-submit-hook>"""

    return None


# =============================================================================
# MAIN
# =============================================================================

def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    output_parts = []

    # SYSTEM 1: Scheduled reminders (counter-based, independent of user message)
    increment_counter()
    reminder = check_and_consume_reminder()
    if reminder:
        output_parts.append(format_scheduled_reminder(reminder))

    # SYSTEM 2: Pattern-based workflows (keyword-based, checks user message)
    prompt = hook_input.get("prompt", "")
    workflow_output = check_workflow_patterns(prompt)
    if workflow_output:
        output_parts.append(workflow_output)

    # Output both if applicable
    if output_parts:
        print("\n\n".join(output_parts))

    sys.exit(0)


if __name__ == "__main__":
    main()