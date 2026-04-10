"""MGCP - Memory Graph Core Primitives.

Persistent graph-based memory for LLM interactions via MCP.
"""

import sys
import warnings

__version__ = "2.0.0"

if sys.version_info >= (3, 13):
    warnings.warn(
        f"MGCP requires Python 3.11 or 3.12. You are running {sys.version_info.major}.{sys.version_info.minor}. "
        "PyTorch (required by sentence-transformers) does not ship wheels for Python 3.13 on Intel Macs. "
        "Create a venv with Python 3.12: python3.12 -m venv .venv",
        RuntimeWarning,
        stacklevel=2,
    )
