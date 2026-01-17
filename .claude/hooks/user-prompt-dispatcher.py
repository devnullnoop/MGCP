#!/usr/bin/env python3
"""Unified UserPromptSubmit hook dispatcher.

This dispatcher replaces three separate hooks:
- git-reminder.py
- catalogue-reminder.py
- task-start-reminder.py

It implements two INDEPENDENT systems:

1. SCHEDULED REMINDERS: LLM sets reminders for itself at anticipated checkpoints.
   These fire based on counter/timer regardless of user message content.
   Can appear ALONGSIDE pattern-based reminders.

2. PATTERN-BASED REMINDERS: Detect keywords and inject contextual guidance.
   Priority order (ONLY ONE fires):
   1. Git operations (highest priority - safety critical)
   2. Workflow triggers (feature/bug/security/code modification)
   3. Catalogue triggers (library/security/decision mentions)

Design rationale:
- Git reminders are highest priority because pushing bad code has external impact
- Workflow reminders take precedence over catalogue because they're more actionable
- Catalogue reminders are lowest because they're advisory, not blocking
- Scheduled reminders are orthogonal - they fire based on LLM's own schedule
"""
import json
import re
import sys
import time
from pathlib import Path

# Reminder state file location
STATE_FILE = Path.home() / ".mgcp" / "reminder_state.json"


# =============================================================================
# SCHEDULED REMINDER SYSTEM (from task-start-reminder.py)
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
# PRIORITY 1: GIT PATTERNS (from git-reminder.py)
# =============================================================================

GIT_KEYWORDS = [
    r"\bcommit\b",
    r"\bpush\b",
    r"\bgit\b",
    r"\bpr\b",
    r"\bpull request\b",
    r"\bmerge\b",
]


def check_git_patterns(prompt: str) -> str | None:
    """Check for git-related keywords. Returns reminder or None."""
    prompt_lower = prompt.lower()

    for pattern in GIT_KEYWORDS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
STOP. Before any git push/commit/PR, execute these commands and SHOW OUTPUT:

```bash
# 1. What changed?
git diff --name-only HEAD~1

# 2. If ANY .md files changed, run this and SHOW OUTPUT:
git diff HEAD~1 -- "*.md" | head -100
```

PASTE THE OUTPUT ABOVE before running git commands.

If the diff shows terminology changes (e.g., ChromaDB->Qdrant), RUN:
```bash
grep -rn "OLD_TERM" *.md README.md CONTRIBUTING.md CLAUDE.md 2>/dev/null
```

Fix any stale references BEFORE pushing. The git command must appear AFTER the grep output.
</user-prompt-submit-hook>"""

    return None


# =============================================================================
# PRIORITY 2: WORKFLOW PATTERNS (from task-start-reminder.py)
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

# Don't trigger workflows on these (git patterns handled separately at higher priority)
WORKFLOW_IGNORE_PATTERNS = [
    r"^(what|where|how|why|can you explain|tell me about)\b",
    r"\b(read|show|display|list|check)\b.{0,10}$",
]


def check_workflow_patterns(prompt: str) -> str | None:
    """Check if prompt matches any workflow patterns. Returns workflow output or None."""
    prompt_lower = prompt.lower().strip()

    # Skip ignored patterns
    for pattern in WORKFLOW_IGNORE_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return None

    # Check feature development
    for pattern in FEATURE_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
STOP. This task requires the feature-development workflow.

BEFORE writing any code, execute these commands and SHOW OUTPUT:

1. Call mcp__mgcp__get_workflow("feature-development") - SHOW OUTPUT
2. Call mcp__mgcp__query_lessons with task description - SHOW OUTPUT
3. Create TodoWrite entries for each workflow step - SHOW THE TODO LIST

The workflow output and lesson query results MUST appear in your response BEFORE any code.

Then for EACH step: call get_workflow_step(step_id, expand_lessons=true) and SHOW OUTPUT before executing that step.
</user-prompt-submit-hook>"""

    # Check bug fix
    for pattern in BUG_FIX_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
STOP. This task requires the bug-fix workflow.

BEFORE attempting any fix, execute these commands and SHOW OUTPUT:

1. Call mcp__mgcp__get_workflow("bug-fix") - SHOW OUTPUT
2. Call mcp__mgcp__query_lessons with bug description - SHOW OUTPUT
3. Create TodoWrite entries for: Reproduce, Investigate, Fix, Verify - SHOW THE TODO LIST

The workflow output and lesson query results MUST appear in your response BEFORE any fix attempts.

CRITICAL: Understand the root cause BEFORE fixing. Show your investigation output.
</user-prompt-submit-hook>"""

    # Check security review
    for pattern in SECURITY_REVIEW_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
STOP. This task requires the secure-code-review workflow (OWASP-based).

BEFORE reviewing any code, execute these commands and SHOW OUTPUT:

1. Call mcp__mgcp__get_workflow("secure-code-review") - SHOW OUTPUT
2. Call mcp__mgcp__query_lessons("security vulnerabilities OWASP") - SHOW OUTPUT
3. Create TodoWrite entries for all 8 steps - SHOW THE TODO LIST

For EACH step: call get_workflow_step(step_id, expand_lessons=true) and SHOW OUTPUT.
Document findings with add_catalogue_security_note and SHOW OUTPUT.
</user-prompt-submit-hook>"""

    # Check generic code modification
    for pattern in CODE_MODIFICATION_PATTERNS:
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return """<user-prompt-submit-hook>
STOP. This task involves code modifications.

BEFORE writing any code, execute this and SHOW OUTPUT:

1. Call mcp__mgcp__query_workflows with task description - SHOW OUTPUT

If a workflow matches (relevance >= 50%):
- Call get_workflow for that workflow - SHOW OUTPUT
- Create TodoWrite entries - SHOW THE TODO LIST
- Proceed step by step, showing each step's output

If NO workflow applies, state: "No workflow applies because: [specific reason]"
(e.g., "single-line typo fix", "documentation-only change")
</user-prompt-submit-hook>"""

    return None


# =============================================================================
# PRIORITY 3: CATALOGUE PATTERNS (from catalogue-reminder.py)
# =============================================================================

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

CATALOGUE_REMINDER_TEMPLATES = {
    "dependency": "REQUIRED: Call mcp__mgcp__search_catalogue with query about this dependency. SHOW OUTPUT. If not found, call add_catalogue_dependency.",
    "security": "REQUIRED: Call mcp__mgcp__add_catalogue_security_note NOW. SHOW OUTPUT. Do not proceed without documenting this.",
    "decision": "REQUIRED: Call mcp__mgcp__add_catalogue_decision NOW. SHOW OUTPUT. Include rationale and alternatives considered.",
    "arch_note": "REQUIRED: Call mcp__mgcp__add_catalogue_arch_note NOW. SHOW OUTPUT. Capture the gotcha/pattern before you forget.",
    "convention": "REQUIRED: Call mcp__mgcp__add_catalogue_convention NOW. SHOW OUTPUT. Document the rule.",
    "coupling": "REQUIRED: Call mcp__mgcp__add_catalogue_coupling NOW. SHOW OUTPUT. List the coupled files.",
    "prior_decision": "STOP. User referenced a prior decision. Call mcp__mgcp__search_catalogue to find it. SHOW OUTPUT. If not found, call add_catalogue_decision NOW before proceeding.",
}


def check_catalogue_patterns(prompt: str) -> str | None:
    """Check for catalogue-worthy mentions. Returns reminder or None."""
    detected = set()

    for pattern, catalogue_type in CATALOGUE_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            detected.add(catalogue_type)

    if detected:
        reminders = [CATALOGUE_REMINDER_TEMPLATES[t] for t in detected]
        lines = [
            "<user-prompt-submit-hook>",
            "Catalogue reminder - you mentioned something that might be worth documenting:",
        ]
        for reminder in reminders:
            lines.append(f"  - {reminder}")
        lines.append("</user-prompt-submit-hook>")
        return "\n".join(lines)

    return None


# =============================================================================
# MAIN DISPATCHER
# =============================================================================

def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    output_parts = []

    # SYSTEM 1: Scheduled reminders (counter-based, independent of user message)
    # Always runs - orthogonal to pattern matching
    increment_counter()
    reminder = check_and_consume_reminder()
    if reminder:
        output_parts.append(format_scheduled_reminder(reminder))

    # SYSTEM 2: Pattern-based reminders (keyword-based, checks user message)
    # Priority order: git > workflow > catalogue
    # ONLY ONE pattern-based reminder fires (highest priority match wins)
    prompt = hook_input.get("prompt", "")

    pattern_reminder = None

    # Priority 1: Git (safety critical)
    pattern_reminder = check_git_patterns(prompt)

    # Priority 2: Workflow (actionable process guidance)
    if pattern_reminder is None:
        pattern_reminder = check_workflow_patterns(prompt)

    # Priority 3: Catalogue (advisory)
    if pattern_reminder is None:
        pattern_reminder = check_catalogue_patterns(prompt)

    if pattern_reminder:
        output_parts.append(pattern_reminder)

    # Output all parts (scheduled reminder + at most one pattern reminder)
    if output_parts:
        print("\n\n".join(output_parts))

    sys.exit(0)


if __name__ == "__main__":
    main()