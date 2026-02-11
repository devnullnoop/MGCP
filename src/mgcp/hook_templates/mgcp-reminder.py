#!/usr/bin/env python3
"""PostToolUse hook - proof-based checkpoint after code changes."""

print("""STOP. After this edit, answer these questions OUT LOUD before continuing:

1. What file(s) did I just change? [list them]
2. Did I discover a pattern, gotcha, or coupling? [yes/no]
3. If yes, call the appropriate MGCP tool NOW and show its output:
   - Pattern/gotcha: mcp__mgcp__add_catalogue_item(item_type="arch", ...)
   - Files coupled: mcp__mgcp__add_catalogue_item(item_type="coupling", ...)
   - Decision made: mcp__mgcp__add_catalogue_item(item_type="decision", ...)

If no knowledge worth capturing, state: "No new knowledge from this edit."

DO NOT proceed to next action until you've answered above.""")
