# Self-Directed Reminders

## Overview

Self-directed reminders allow the LLM to schedule future reminders for itself. This enables autonomous workflow execution across multi-turn conversations without relying on user message patterns.

## The Problem

The original reminder system had a limitation:

1. LLM suppresses reminders for N calls with `set_reminder_boundary(suppress_for_calls=5)`
2. After 5 calls, suppression expires
3. **But reminders only fire if the user's message matches patterns** (e.g., "fix", "implement")
4. If no pattern matches, no reminder fires - the LLM may forget its workflow step

## The Solution

Extended `set_reminder_boundary` to accept:
- `message`: Custom reminder text to inject when boundary expires
- `lesson_ids`: Comma-separated lesson IDs to surface
- `workflow_step`: Workflow/step to load (e.g., "bug-fix/investigate")

When the boundary expires, the hook injects the LLM's own reminder **regardless of user message content**.

## How It Works

### Flow Diagram

```
1. LLM at workflow step 1 (Research)
   │
   ├─▶ Completes research
   │
   ├─▶ Calls set_reminder_boundary(
   │       suppress_for_calls=2,
   │       message="Execute step 2: Plan the implementation",
   │       lesson_ids="error-handling,verification",
   │       workflow_step="feature-development/plan"
   │   )
   │
   ├─▶ User sends 2 messages (any content)
   │
   └─▶ Hook fires, injects:
       ┌─────────────────────────────────────────────┐
       │ 🔔 SELF-DIRECTED REMINDER                   │
       │                                             │
       │ Message: Execute step 2: Plan the impl...  │
       │                                             │
       │ Workflow Step to Execute:                   │
       │   Call get_workflow_step("feature-dev...")  │
       │                                             │
       │ Lessons to Surface:                         │
       │   - get_lesson("error-handling")            │
       │   - get_lesson("verification")              │
       └─────────────────────────────────────────────┘
```

### API

```python
# Schedule a self-directed reminder
set_reminder_boundary(
    suppress_for_calls=2,              # Remind after 2 hook checks
    # OR
    suppress_for_minutes=5,            # Remind after 5 minutes

    # New fields:
    message="Your reminder text",      # What to remind about
    lesson_ids="lesson-a,lesson-b",    # Lessons to surface
    workflow_step="workflow/step",     # Workflow step to load
    note="Optional task context"       # Context for reference
)
```

### State File

The reminder state is persisted in `~/.mgcp/reminder_state.json`:

```json
{
  "mode": "counter",
  "suppress_until_call": 5,
  "current_call_count": 3,
  "task_note": "Implementing auth feature",
  "reminder_message": "Execute step 2: Plan",
  "lesson_ids": ["error-handling", "verification"],
  "workflow_step": "feature-development/plan",
  "last_updated": "2026-01-10T12:00:00Z"
}
```

## Use Cases

### 1. Workflow Step Progression

```python
# At end of Research step:
set_reminder_boundary(
    suppress_for_calls=1,
    message="Move to Plan step. Design the implementation approach.",
    workflow_step="feature-development/plan"
)
```

### 2. Context-Dependent Lessons

```python
# When about to write error handling:
set_reminder_boundary(
    suppress_for_calls=2,
    message="Remember specific error handling patterns",
    lesson_ids="specific-exceptions,error-context,validate-input"
)
```

### 3. Multi-Step Task Tracking

```python
# After fixing first bug in a batch:
set_reminder_boundary(
    suppress_for_calls=3,
    message="Continue with remaining type errors. 8 of 10 fixed.",
    note="Fixing type errors in auth module"
)
```

## Design Decisions

### Why Consume After Firing?

The reminder data is cleared after injection to prevent repeated firing. If the LLM wants another reminder, it must explicitly set a new one.

### Why Priority Over Pattern Matching?

If a pending reminder exists when the boundary expires, it takes priority over pattern-based workflow detection. The LLM explicitly scheduled this reminder and should receive it.

### Why Not Fire Immediately?

The delay (calls/minutes) gives the LLM time to complete current work before being reminded. Immediate reminders would be disruptive.

## Relationship to Workflows

This feature bridges two concepts:

1. **Workflow Steps** (rigid, sequential): Research → Plan → Execute → Test → Review
2. **Semantic Lessons** (loose, associative): Related concepts, gotchas, patterns

Self-directed reminders let the LLM:
- Follow workflow structure (via `workflow_step`)
- Surface relevant lessons at each step (via `lesson_ids`)
- Stay on track across conversation turns (via `message`)

## Testing

To test manually:

```bash
# Set a reminder
mcp__mgcp__set_reminder_boundary(
    suppress_for_calls=2,
    message="Test reminder",
    lesson_ids="verification",
    workflow_step="bug-fix/investigate"
)

# Send 2 user messages (any content)
# Observe the self-directed reminder injection
```

## Files Changed

- `src/mgcp/reminder_state.py`: Added new fields and `check_and_consume_reminder()`
- `src/mgcp/server.py`: Extended MCP tool with new parameters
- `.claude/hooks/task-start-reminder.py`: Added `format_self_directed_reminder()` and consumption logic
