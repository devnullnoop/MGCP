"""Pytest configuration for MGCP tests.

This module handles cleanup of resources after tests complete.
With Qdrant (replacing ChromaDB), we no longer need the aggressive thread cleanup
hack that was required for ChromaDB's background threads.
"""

import gc


def pytest_sessionfinish(session, exitstatus):
    """Clean up resources after all tests complete."""
    # Force garbage collection to clean up any lingering objects
    gc.collect()
