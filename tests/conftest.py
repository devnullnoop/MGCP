"""Pytest configuration for MGCP tests.

This module handles cleanup of resources after tests complete.
With Qdrant (replacing ChromaDB), we no longer need the aggressive thread cleanup
hack that was required for ChromaDB's background threads.
"""

import gc
import re
from pathlib import Path


def pytest_sessionfinish(session, exitstatus):
    """Clean up resources, then hold the claim ledger to its own tests."""
    # Force garbage collection to clean up any lingering objects
    gc.collect()
    _assert_ledger_matches_its_tests(session)


# ---------------------------------------------------------------------------
# The claim ledger must not lie about itself
# ---------------------------------------------------------------------------

_LEDGER = Path(__file__).resolve().parents[1] / "docs" / "CAPABILITIES.md"
_CLAIM_OUTCOMES: dict[str, bool] = {}


def pytest_runtest_logreport(report):
    """Record pass/fail for every test_<ID>_* in the claim ledger suite."""
    if report.when != "call":
        return
    m = re.search(r"::test_([CE]\d\d)_", report.nodeid)
    if m:
        _CLAIM_OUTCOMES[m.group(1)] = report.passed


def _assert_ledger_matches_its_tests(session):
    """Fail the run if docs/CAPABILITIES.md disagrees with its own tests.

    The ledger is the instrument that stops MGCP's claims rotting — so it has
    to be able to detect its OWN rot, and it could not: nothing parsed the
    Status column. Within an hour of the ledger being written three rows had
    already drifted. C02 and C04 sat at FAKE after the underlying docs were
    fixed; C24 recorded a retrieval floor of 0.55 after the code went back to
    0.30.

    A row claiming FAKE whose test passes is as much a lie as a row claiming
    VERIFIED whose test fails, so both directions are checked.

    This is a sessionfinish hook rather than a test on purpose: the earlier
    attempt spawned pytest inside pytest, which deadlocked against the outer
    run on the Qdrant file locks. Here the outcomes are already in hand.

    Only runs when the claim suite was actually collected, so `pytest -k
    something_else` is not derailed by it. Rows with no test_<ID>_* function
    are prose judgements a machine cannot settle; they are reported, not
    enforced.
    """
    if not _CLAIM_OUTCOMES or not _LEDGER.exists():
        return

    ledger: dict[str, str] = {}
    for line in _LEDGER.read_text().splitlines():
        m = re.match(r"\|\s*([CE]\d\d)\s*\|.*\*\*([A-Z]+)\*\*", line)
        if m:
            ledger[m.group(1)] = m.group(2)

    wrong = []
    for cid, passed in sorted(_CLAIM_OUTCOMES.items()):
        status = ledger.get(cid)
        if status == "VERIFIED" and not passed:
            wrong.append(f"{cid}: ledger says VERIFIED, its test FAILS")
        elif status == "FAKE" and passed:
            wrong.append(f"{cid}: ledger says FAKE, its test PASSES — fixed and not recorded")

    if wrong:
        session.exitstatus = 1
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_sep("=", "CAPABILITIES.md disagrees with its own tests", red=True)
            for w in wrong:
                reporter.write_line(f"  {w}")
            reporter.write_line(
                "  Update the Status column, or fix the claim. A stale ledger "
                "is worse than none — it is a false statement with a test "
                "next to it."
            )
