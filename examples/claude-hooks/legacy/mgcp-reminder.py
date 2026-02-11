#!/usr/bin/env python3
"""PostToolUse hook - proof-based checkpoint after code changes."""

print("""STOP. You are a knowledge-capturing agent. Every code change is a potential lesson.

1. LIST the file(s) you just changed.
2. STATE any pattern, gotcha, or coupling you discovered.
3. CALL the appropriate MGCP tool for each finding:
   - Pattern/gotcha: add_catalogue_item(item_type="arch", ...)
   - Files coupled: add_catalogue_item(item_type="coupling", ...)
   - Decision made: add_catalogue_item(item_type="decision", ...)

Do NOT proceed until steps 1-3 are complete.""")
