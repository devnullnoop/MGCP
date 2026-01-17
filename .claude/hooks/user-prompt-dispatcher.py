#!/usr/bin/env python3
"""Phase-based UserPromptSubmit dispatcher.

This dispatcher implements a sequential phase model, NOT priority-based pattern matching.

PHASES:
1. WORKFLOW_DETECTION - Every task starts here. Query workflows, activate if match.
2. STEP_EXECUTION - Workflow active. Surface lessons for current step.
3. ERROR_CAPTURE - Error detected. Immediately prompt for lesson creation.
4. COMPLETION - Workflow done. Git checks + documentation.

The key insight: Git checks aren't a competing priority - they're the LAST STEP
of workflows that end in commits. Phases are sequential, not competing.

State is persisted in ~/.mgcp/workflow_state.json between messages.
"""
import json
import re
import sys
import time
from pathlib import Path

# State files
STATE_FILE = Path.home() / ".mgcp" / "workflow_state.json"

# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def _load_state() -> dict:
    """Load workflow state from file."""
    defaults = {
        # Scheduled reminder fields (preserved from old system)
        "current_call_count": 0,
        "remind_at_call": 0,
        "remind_at_time": 0,
        "reminder_message": "",
        "lesson_ids": [],
        "workflow_step": "",
        "task_note": "",

        # NEW: Workflow state fields
        "active_workflow": None,      # e.g., "feature-development"
        "current_step": None,         # e.g., "research"
        "workflow_complete": False,
        "steps_completed": [],
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


# =============================================================================
# SCHEDULED REMINDERS (preserved from old system)
# =============================================================================

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
        "SCHEDULED REMINDER (self-directed)",
        "",
    ]

    if reminder.get("message"):
        lines.append(f"**Message:** {reminder['message']}")
        lines.append("")

    if reminder.get("workflow_step"):
        workflow_step = reminder["workflow_step"]
        if "/" in workflow_step:
            workflow_id, step_id = workflow_step.split("/", 1)
            lines.append(f"**Next Step:** Call get_workflow_step(\"{workflow_id}\", \"{step_id}\", expand_lessons=true)")
        else:
            lines.append(f"**Workflow:** Call get_workflow(\"{workflow_step}\")")
        lines.append("")

    if reminder.get("lesson_ids"):
        lesson_ids = reminder["lesson_ids"]
        if isinstance(lesson_ids, str):
            lesson_ids = [lid.strip() for lid in lesson_ids.split(",") if lid.strip()]
        lines.append("**Lessons:** " + ", ".join(lesson_ids))
        lines.append("")

    lines.append("</scheduled-reminder>")
    return "\n".join(lines)


# =============================================================================
# PHASE DETECTION
# =============================================================================

# Error signals - triggers immediate lesson capture
ERROR_PATTERNS = [
    r"\b(error|exception|traceback|failed|failure|broken|crashed|bug)\b",
    r"\b(doesn't work|won't work|not working|can't|cannot)\b",
    r"\b(wrong|incorrect|unexpected|weird|strange)\b.{0,20}\b(result|output|behavior)\b",
]

# Git keywords - only relevant in COMPLETION phase
GIT_PATTERNS = [
    r"\bcommit\b",
    r"\bpush\b",
    r"\bgit\s+(add|status|diff|log)\b",
    r"\bpr\b",
    r"\bpull request\b",
    r"\bmerge\b",
]

# Task start patterns - suggest workflow detection needed
TASK_START_PATTERNS = [
    r"\b(implement|add|create|build|fix|debug|refactor|update|change)\b",
    r"\blet'?s\s+(work on|start|begin|do)\b",
    r"\bcan you\b.{0,20}\b(help|make|write|add)\b",
    r"\bnew\s+(feature|function|component)\b",
]


def contains_error_signal(prompt: str) -> bool:
    """Check if prompt contains error/failure signals."""
    prompt_lower = prompt.lower()
    for pattern in ERROR_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True
    return False


def contains_git_keywords(prompt: str) -> bool:
    """Check if prompt contains git operation keywords."""
    prompt_lower = prompt.lower()
    for pattern in GIT_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True
    return False


def contains_task_start(prompt: str) -> bool:
    """Check if prompt suggests starting a new task."""
    prompt_lower = prompt.lower()
    for pattern in TASK_START_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True
    return False


def detect_phase(prompt: str, state: dict) -> str:
    """Determine which phase we're in based on state and prompt content."""

    active_workflow = state.get("active_workflow")
    workflow_complete = state.get("workflow_complete", False)

    # PHASE 3: ERROR_CAPTURE (highest priority - learn immediately)
    # Only if we're in an active workflow and hit an error
    if active_workflow and not workflow_complete and contains_error_signal(prompt):
        return "ERROR_CAPTURE"

    # PHASE 4: COMPLETION (workflow done + git keywords)
    if workflow_complete and contains_git_keywords(prompt):
        # Reset workflow state since we're completing
        state["active_workflow"] = None
        state["current_step"] = None
        state["workflow_complete"] = False
        state["steps_completed"] = []
        _save_state(state)
        return "COMPLETION"

    # PHASE 2: STEP_EXECUTION (workflow active, not complete)
    if active_workflow and not workflow_complete:
        return "STEP_EXECUTION"

    # PHASE 1: WORKFLOW_DETECTION (no active workflow, or new task detected)
    # If git keywords but no workflow was active, also go to workflow detection
    # (user might be saying "commit this" for a quick fix - still check workflow first)
    if contains_task_start(prompt) or contains_git_keywords(prompt):
        return "WORKFLOW_DETECTION"

    # Default: no phase-specific output needed (simple questions, etc.)
    return "NONE"


# =============================================================================
# PHASE OUTPUTS
# =============================================================================

def phase_workflow_detection(prompt: str) -> str:
    """Output for WORKFLOW_DETECTION phase."""
    return """<user-prompt-submit-hook>
STARTING TASK. First, determine the workflow.

1. Call mcp__mgcp__query_workflows("<brief task description>") - SHOW OUTPUT

If relevance ≥50%:
  - Call mcp__mgcp__get_workflow("<workflow_id>") - SHOW OUTPUT
  - Create TodoWrite entries for each step
  - Begin with Step 1

If no workflow matches:
  - State: "No workflow matches. This is a [simple fix / question / etc]."
  - OR ask: "Should I create a workflow for this task type?"

SHOW the workflow query output before proceeding.
</user-prompt-submit-hook>"""


def phase_step_execution(state: dict) -> str:
    """Output for STEP_EXECUTION phase."""
    workflow = state.get("active_workflow", "unknown")
    step = state.get("current_step", "unknown")
    completed = state.get("steps_completed", [])

    completed_str = ", ".join(completed) if completed else "none"

    return f"""<user-prompt-submit-hook>
WORKFLOW ACTIVE: {workflow}
CURRENT STEP: {step}
COMPLETED: {completed_str}

For this step, call:
  mcp__mgcp__get_workflow_step("{workflow}", "{step}", expand_lessons=true) - SHOW OUTPUT

Surface relevant lessons. Complete checklist items.
When step is done, update state and advance to next step.

On ERROR: Immediately capture as lesson before continuing.
</user-prompt-submit-hook>"""


def phase_error_capture() -> str:
    """Output for ERROR_CAPTURE phase."""
    return """<user-prompt-submit-hook>
ERROR DETECTED. Capture this learning NOW.

Before fixing, answer:
1. What went wrong? [specific error]
2. Why did it happen? [root cause]
3. How to prevent it? [the lesson]

If this is a reusable lesson, call:
  mcp__mgcp__add_lesson(
    id="<descriptive-id>",
    trigger="<when this applies>",
    action="<what to do>",
    rationale="<why this matters>"
  ) - SHOW OUTPUT

If project-specific, use add_catalogue_arch_note instead.

THEN fix the error.
</user-prompt-submit-hook>"""


def phase_completion() -> str:
    """Output for COMPLETION phase."""
    return """<user-prompt-submit-hook>
WORKFLOW COMPLETE. Final checks before git operation.

1. What changed? Run and SHOW OUTPUT:
   git diff --name-only HEAD~1

2. If .md files changed, check for stale terms:
   git diff HEAD~1 -- "*.md" | head -50

3. Save context:
   mcp__mgcp__save_project_context with notes about what was accomplished

SHOW the outputs above, then proceed with git operation.
</user-prompt-submit-hook>"""


# =============================================================================
# MAIN DISPATCHER
# =============================================================================

def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    output_parts = []
    prompt = hook_input.get("prompt", "")

    # Always increment counter for scheduled reminders
    increment_counter()

    # Check for scheduled reminders (orthogonal to phases)
    reminder = check_and_consume_reminder()
    if reminder:
        output_parts.append(format_scheduled_reminder(reminder))

    # Load state and detect phase
    state = _load_state()
    phase = detect_phase(prompt, state)

    # Generate phase-appropriate output
    if phase == "WORKFLOW_DETECTION":
        output_parts.append(phase_workflow_detection(prompt))
    elif phase == "STEP_EXECUTION":
        output_parts.append(phase_step_execution(state))
    elif phase == "ERROR_CAPTURE":
        output_parts.append(phase_error_capture())
    elif phase == "COMPLETION":
        output_parts.append(phase_completion())
    # phase == "NONE" produces no output

    if output_parts:
        print("\n\n".join(output_parts))

    sys.exit(0)


if __name__ == "__main__":
    main()