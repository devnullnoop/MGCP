#!/usr/bin/env python3
"""PostToolUse hook - contextual reminder after code changes."""

print("""[MGCP Checkpoint] After this edit, consider:
  • Discovered a pattern/gotcha? → add_catalogue_arch_note
  • Files that change together? → add_catalogue_coupling
  • Made a design decision? → add_catalogue_decision
  • Learned something reusable? → add_lesson""")
