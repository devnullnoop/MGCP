#!/bin/bash
# PreCompact hook - CRITICAL reminder before context compression

cat << 'EOF'

╔════════════════════════════════════════════════════════════════════╗
║  CONTEXT COMPRESSION IMMINENT - SAVE YOUR LESSONS NOW              ║
╠════════════════════════════════════════════════════════════════════╣
║  Before this context compresses, you MUST:                         ║
║                                                                    ║
║  1. ADD any lessons learned this session:                          ║
║     → mcp__mgcp__add_lesson                                        ║
║                                                                    ║
║  2. SAVE project context for next session:                         ║
║     → mcp__mgcp__save_project_context                              ║
║                                                                    ║
║  If you learned ANYTHING worth remembering, add it NOW or lose it. ║
╚════════════════════════════════════════════════════════════════════╝

EOF