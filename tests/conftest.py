"""Pytest configuration for MGCP tests.

This module handles cleanup of resources after tests complete, points the
suite at a throwaway data directory, and holds the claim ledger to its tests.
With Qdrant (replacing ChromaDB), we no longer need the aggressive thread
cleanup hack that was required for ChromaDB's background threads.
"""

import gc
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# The suite does not get to touch the operator's data
#
# MGCP_DATA_DIR selects the lessons DB, both Qdrant stores, the enforcement
# rules and the intent config, and every DEFAULT_* path is bound at import
# time -- so this has to happen before anything imports mgcp, which is why it
# runs at conftest import rather than in a fixture.
#
# It was not theoretical. web_server.py calls LessonStore() with no path, and
# test_api_ui_integration imports that app, so the suite opened
# ~/.mgcp/lessons.db directly. That was survivable while it only ever read;
# on 2026-07-29 a repair migration was added to store open, and a plain
# `pytest` run silently rewrote the operator's REM schedule rows. The same
# reach is why five tests failed whenever the MCP server held the Qdrant lock:
# they were queueing for the live store.
#
# Tests that inspect the real install on purpose (the claim ledger, read-only)
# read MGCP_LIVE_DATA_DIR, which still points where the operator's data
# actually is. Redirecting them too would turn real verification into a
# silent skip, which is worse than the disease.
_LIVE_DATA_DIR = os.environ.get("MGCP_DATA_DIR") or str(Path.home() / ".mgcp")
_SANDBOX_DATA_DIR = tempfile.mkdtemp(prefix="mgcp-tests-")
os.environ["MGCP_LIVE_DATA_DIR"] = _LIVE_DATA_DIR
os.environ["MGCP_DATA_DIR"] = _SANDBOX_DATA_DIR

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger import LEDGER_PATH, parse_rows  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """Clean up resources, then hold the claim ledger to its own tests."""
    # Force garbage collection to clean up any lingering objects
    gc.collect()
    shutil.rmtree(_SANDBOX_DATA_DIR, ignore_errors=True)
    _assert_ledger_matches_its_tests(session)


# ---------------------------------------------------------------------------
# The claim ledger must not lie about itself
# ---------------------------------------------------------------------------

_LEDGER = LEDGER_PATH
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

    ledger = parse_rows(_LEDGER.read_text())

    wrong = []
    for cid, passed in sorted(_CLAIM_OUTCOMES.items()):
        status = ledger.get(cid)
        if status is None:
            # A test exists for a row the parser cannot see. That is how the
            # check went blind before: rows tagged `| C08 ✅ |` failed to
            # match and were silently exempted rather than reported.
            wrong.append(f"{cid}: has a test but no row this checker can read")
        elif status == "VERIFIED" and not passed:
            wrong.append(f"{cid}: ledger says VERIFIED, its test FAILS")
        elif status == "FAKE" and passed:
            wrong.append(f"{cid}: ledger says FAKE, its test PASSES — fixed and not recorded")
        elif status == "RETRACTED" and passed:
            wrong.append(f"{cid}: ledger says RETRACTED, its test still PASSES")

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
