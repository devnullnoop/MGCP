#!/bin/bash
# PostToolUse hook - contextual reminder after code changes

cat << 'EOF'
[MGCP Checkpoint] After this edit, consider:
  • Discovered a pattern/gotcha? → add_catalogue_arch_note
  • Files that change together? → add_catalogue_coupling
  • Made a design decision? → add_catalogue_decision
  • Learned something reusable? → add_lesson
EOF