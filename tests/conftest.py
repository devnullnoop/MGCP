"""Pytest configuration for MGCP tests.

This module handles cleanup of resources that can prevent pytest from exiting,
particularly ChromaDB's background threads.
"""

import gc
import sys


def pytest_sessionfinish(session, exitstatus):
    """Clean up resources after all tests complete.

    ChromaDB's PersistentClient creates background threads that can prevent
    pytest from exiting cleanly. This hook ensures those threads are stopped.
    """
    # Force garbage collection to clean up any lingering objects
    gc.collect()

    # Try to clean up chromadb's thread pool if it exists
    try:
        import chromadb
        # Reset chromadb's internal state
        if hasattr(chromadb, '_client'):
            chromadb._client = None
    except Exception:
        pass

    # Force cleanup of any remaining threads
    gc.collect()

    # Give threads a moment to clean up, then force exit if tests passed
    # This prevents hanging on CI when background threads don't terminate
    if exitstatus == 0:
        import threading
        import time

        # Wait briefly for threads to clean up naturally
        time.sleep(0.5)

        # Check if there are non-daemon threads still running
        active_threads = [t for t in threading.enumerate()
                        if t.is_alive() and not t.daemon and t.name != 'MainThread']

        if active_threads:
            # Log which threads are hanging
            print(f"\nWarning: {len(active_threads)} background threads still active after tests:",
                  file=sys.stderr)
            for t in active_threads:
                print(f"  - {t.name}", file=sys.stderr)

            # Force exit - tests passed, but cleanup hung
            import os
            os._exit(0)
