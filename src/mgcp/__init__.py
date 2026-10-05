"""MGCP - Memory Graph Core Primitives.

Persistent graph-based memory for LLM interactions via MCP.
"""

import platform
import sys
import warnings

__version__ = "3.0.0"

# The Python ceiling is a property of the machine, not of MGCP.
#
# MGCP needs sentence-transformers, which needs PyTorch. On an Intel Mac,
# PyTorch 2.2.2 is the last release with any x86_64 wheel and its newest
# interpreter tag is cp312. PyTorch dropped the platform after that, so Python
# 3.12 is the permanent ceiling there and no amount of waiting changes it.
#
# Everywhere else, including Apple Silicon, PyTorch ships wheels well past 3.12.
# A single `<3.13` cap therefore punished every modern machine for a limit that
# only Intel Macs have. requires-python cannot vary by architecture, so the cap
# is set at the highest version this project tests and the architecture check
# happens here.
PYTHON_CEILING = (3, 13)
INTEL_MAC_PYTHON_CEILING = (3, 12)


def _is_intel_mac() -> bool:
    """True on a macOS machine with an Intel processor.

    Reads the machine type rather than the interpreter build. A 3.13 build has no
    Rosetta story worth relying on, so x86_64 here means x86_64 wheels are what
    pip will look for.
    """
    return sys.platform == "darwin" and platform.machine() in ("x86_64", "i386")


def _unsupported_python() -> str:
    """Why this interpreter will not work, or "" when it will.

    Returns prose for a person to act on, naming the real cause. The caller
    decides whether that becomes a warning or an error.
    """
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if _is_intel_mac() and sys.version_info[:2] > INTEL_MAC_PYTHON_CEILING:
        return (
            f"You are running Python {running} on an Intel Mac, where MGCP needs "
            "Python 3.11 or 3.12. PyTorch, which sentence-transformers needs, "
            "stopped shipping Intel Mac wheels after version 2.2.2, and that "
            "release supports no interpreter past 3.12. This ceiling is "
            "permanent on this hardware. Create a 3.12 environment: "
            "python3.12 -m venv .venv"
        )
    if sys.version_info[:2] > PYTHON_CEILING:
        return (
            f"You are running Python {running}, which MGCP has not been tested "
            f"against. Supported versions are 3.11 to {PYTHON_CEILING[0]}."
            f"{PYTHON_CEILING[1]}. It may work. If it does not, create a "
            "supported environment: python3.12 -m venv .venv"
        )
    return ""


_problem = _unsupported_python()
if _problem:
    warnings.warn(_problem, RuntimeWarning, stacklevel=2)
del _problem
