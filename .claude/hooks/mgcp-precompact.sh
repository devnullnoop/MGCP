#!/bin/bash
# PreCompact hook - CRITICAL reminder before context compression

cat << 'EOF'

╔══════════════════════════════════════════════════════════════════════════════╗
║  CONTEXT COMPRESSION IMMINENT - SAVE YOUR STATE NOW                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  REQUIRED ACTIONS before compression:                                        ║
║                                                                              ║
║  1. SAVE PROJECT CONTEXT (always do this):                                   ║
║     → mcp__mgcp__save_project_context                                        ║
║       - notes: "What was accomplished this session"                          ║
║       - active_files: "file1.py, file2.py"                                   ║
║       - decision: "Any key decisions made"                                   ║
║                                                                              ║
║  2. ADD LESSONS for anything reusable you learned:                           ║
║     → mcp__mgcp__add_lesson (id, trigger, action, rationale)                 ║
║                                                                              ║
║  3. ADD CATALOGUE ITEMS you discovered:                                      ║
║     → Decisions: mcp__mgcp__add_catalogue_decision                           ║
║     → Couplings: mcp__mgcp__add_catalogue_coupling                           ║
║     → Gotchas:   mcp__mgcp__add_catalogue_arch_note                          ║
║     → Security:  mcp__mgcp__add_catalogue_security_note                      ║
║                                                                              ║
║  ⚠️  If you don't save now, this context will be LOST after compression.     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

EOF