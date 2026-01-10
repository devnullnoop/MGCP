# Self-Directed Reminders

## Overview

Self-directed reminders allow the LLM to schedule future reminders for itself at anticipated checkpoints. This enables workflow continuity across multi-turn conversations.

## The Problem

Pattern-based workflow hooks only fire when user messages contain keywords like "implement" or "fix". But what if:

1. LLM finishes Research step, knows it needs Plan step lessons next
2. User says "ok continue" (no keywords)
3. Pattern hook doesn't fire → LLM forgets to load Plan step knowledge

## The Solution

**One tool:** `schedule_reminder` - schedule knowledge to surface at a future checkpoint.

**Two independent systems:**
1. **Scheduled Reminders**: Counter-based, fires regardless of user message content
2. **Pattern Workflows**: Keyword-based, fires based on user message patterns

Both can fire on the same message. They are **additive**, not mutually exclusive.

## How It Works

```
LLM at workflow step 1 (Research)
  │
  ├─▶ Completes research, knows Plan step needs specific lessons
  │
  ├─▶ Calls schedule_reminder(
  │       after_calls=1,
  │       message="Move to Plan step",
  │       lesson_ids="verification,error-handling",
  │       workflow_step="feature-development/plan"
  │   )
  │
  ├─▶ User sends any message (even "ok" or "continue")
  │
  └─▶ Hook injects scheduled reminder:
      ┌─────────────────────────────────────────────┐
      │ 🔔 SCHEDULED REMINDER                       │
      │                                             │
      │ Message: Move to Plan step                  │
      │                                             │
      │ Next Workflow Step:                         │
      │   get_workflow_step("feature-dev", "plan")  │
      │                                             │
      │ Lessons to Surface:                         │
      │   - get_lesson("verification")              │
      │   - get_lesson("error-handling")            │
      └─────────────────────────────────────────────┘

      PLUS any pattern-based workflow if user message matches
```

## API

```python
schedule_reminder(
    after_calls=2,                     # Fire after 2 user messages
    # OR
    after_minutes=5,                   # Fire after 5 minutes

    message="Your reminder text",      # What to remind about
    lesson_ids="lesson-a,lesson-b",    # Lessons to surface
    workflow_step="workflow/step",     # Workflow step to load
    note="Optional task context"       # Context for reference
)
```

## State File

`~/.mgcp/reminder_state.json`:

```json
{
  "current_call_count": 3,
  "remind_at_call": 5,
  "remind_at_time": 0,
  "task_note": "Implementing auth feature",
  "reminder_message": "Move to Plan step",
  "lesson_ids": ["verification", "error-handling"],
  "workflow_step": "feature-development/plan",
  "last_updated": "2026-01-10T12:00:00Z"
}
```

## Use Cases

### 1. Workflow Step Progression

```python
# At end of Research step, schedule knowledge for Plan step:
schedule_reminder(
    after_calls=1,
    message="Execute Plan step - design implementation approach",
    workflow_step="feature-development/plan",
    lesson_ids="verification,error-handling"
)
```

### 2. Multi-Step Task Tracking

```python
# After fixing 2 of 10 errors:
schedule_reminder(
    after_calls=3,
    message="Continue with remaining type errors. 2 of 10 fixed.",
    note="Fixing type errors in auth module"
)
```

### 3. Delayed Knowledge Surfacing

```python
# About to write error handling code:
schedule_reminder(
    after_calls=2,
    message="Apply error handling patterns",
    lesson_ids="specific-exceptions,error-context"
)
```

## Design Decisions

### Why Independent Systems?

**Pattern hooks** catch NEW tasks based on user language.
**Scheduled reminders** continue ONGOING tasks regardless of user language.

Neither should suppress the other. User says "implement the auth" during an active reminder → get BOTH the scheduled reminder AND the feature-development workflow activation.

### Why Consume After Firing?

The reminder clears after injection to prevent repeated firing. If you want another reminder, schedule a new one explicitly.

### Why Counter-Based?

User messages are the natural unit of conversation progression. "After 2 messages" is predictable; "after 5 minutes" is less so because users don't send messages at regular intervals.

## Implementation Plan

### Hook Logic (`task-start-reminder.py`)

```python
def main():
    # SYSTEM 1: Scheduled reminders (always runs)
    increment_counter()
    reminder = check_and_consume_reminder()
    if reminder:
        output_parts.append(format_reminder(reminder))

    # SYSTEM 2: Pattern workflows (always runs, independently)
    workflow_output = check_workflow_patterns(prompt)
    if workflow_output:
        output_parts.append(workflow_output)

    # Output both if applicable
    print("\n\n".join(output_parts))
```

### State Management (`reminder_state.py`)

- `increment_counter()` - called on every hook invocation
- `schedule_reminder(...)` - sets `remind_at_call` and content fields
- `check_and_consume_reminder()` - returns reminder if threshold reached, clears it
- `get_status()` - returns current state for display
- `reset_state()` - clears everything

### MCP Tool (`server.py`)

One tool: `schedule_reminder`
- Sets when reminder fires (after_calls or after_minutes)
- Sets what to inject (message, lesson_ids, workflow_step)
- Returns confirmation with countdown

## Files

- `src/mgcp/reminder_state.py` - State management functions
- `src/mgcp/server.py` - MCP tool `schedule_reminder`
- `.claude/hooks/task-start-reminder.py` - Hook that runs both systems