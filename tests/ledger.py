"""Parsing for docs/CAPABILITIES.md, shared by the ledger's own checks.

One parser, imported by both `conftest.py` (which holds VERIFIED rows to
their passing tests) and `test_claims.py` (which holds the Scoreboard to its
rows). Two copies of this regex would be the same defect the v2.10 detector
fix was about: one bug living in two implementations, both suites green.
"""

from __future__ import annotations

import re
from pathlib import Path

LEDGER_PATH = Path(__file__).resolve().parents[1] / "docs" / "CAPABILITIES.md"

# A claim row. The ID may carry decoration -- rows repaired during a pass are
# tagged `| C08 ✅ |` -- so anything up to the next pipe is skipped. Requiring
# the ID to butt against the pipe is what made the 11 repaired rows, the ones
# most likely to regress, invisible to the drift check that exists to watch
# them. The status is the LAST bolded word on the line: row prose says things
# like "**Was FAKE**", and the Status column comes after it.
_ROW_RE = re.compile(r"\|\s*([CE]\d\d)[^|]*\|.*\*\*([A-Z]+)\*\*")

_SCOREBOARD_RE = re.compile(r"^\|\s*(VERIFIED|CLAIMED|FAKE|RETRACTED)\s*\|\s*(\d+)\s*\|", re.M)
_TOTAL_RE = re.compile(r"^\|\s*\*\*total\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|", re.M)

STATUSES = ("VERIFIED", "CLAIMED", "FAKE", "RETRACTED")


def parse_rows(text: str | None = None) -> dict[str, str]:
    """Map every claim ID in the ledger to its Status column value."""
    if text is None:
        text = LEDGER_PATH.read_text()
    return {m.group(1): m.group(2) for m in (_ROW_RE.match(line) for line in text.splitlines()) if m}


def parse_scoreboard(text: str | None = None) -> dict[str, int]:
    """The Scoreboard table's declared counts, including ``total``."""
    if text is None:
        text = LEDGER_PATH.read_text()
    counts = {m.group(1): int(m.group(2)) for m in _SCOREBOARD_RE.finditer(text)}
    total = _TOTAL_RE.search(text)
    if total:
        counts["total"] = int(total.group(1))
    return counts
