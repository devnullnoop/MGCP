"""Logging configuration for MGCP with log rotation.

This module provides centralized logging configuration with automatic log rotation
to prevent disk space exhaustion. All MGCP components should use this configuration.

Log Rotation Policy:
- Max file size: 10 MB per log file
- Backup count: 5 (keeps mgcp.log, mgcp.log.1, ..., mgcp.log.5)
- Total max disk usage: ~60 MB for logs
- Logs older than the 5th backup are automatically deleted
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Default log configuration.
#
# The log directory follows MGCP_DATA_DIR, like every other file the project
# writes. It was hardcoded to ~/.mgcp/logs and read no environment variable, so
# the test suite wrote to the operator's live log for the whole run even though
# conftest redirects MGCP_DATA_DIR to a sandbox and says "the suite does not get
# to touch the operator's data". server.py calls configure_logging at import
# time, so every test module that imports it attached a handler on that file.
# The handler rotates at 10 MB keeping 5 backups, so a few more full runs would
# have rotated real diagnostic history out to make room for sandbox paths.


def _default_log_dir() -> str:
    """``$MGCP_DATA_DIR/logs``, else ``~/.mgcp/logs``."""
    data_dir = os.environ.get("MGCP_DATA_DIR")
    if data_dir:
        return str(Path(data_dir).expanduser() / "logs")
    return "~/.mgcp/logs"


DEFAULT_LOG_DIR = "~/.mgcp/logs"
DEFAULT_LOG_FILE = "mgcp.log"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
DEFAULT_BACKUP_COUNT = 5  # Keep 5 backup files
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

_configured = False


def configure_logging(
    log_dir: str | None = None,
    log_file: str = DEFAULT_LOG_FILE,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    log_level: int = DEFAULT_LOG_LEVEL,
    log_format: str = DEFAULT_LOG_FORMAT,
    console_output: bool = True,
) -> logging.Logger:
    """Configure MGCP logging with automatic log rotation.

    Args:
        log_dir: Directory for log files (default: $MGCP_DATA_DIR/logs,
            else ~/.mgcp/logs). Resolved per call rather than at import, so
            setting MGCP_DATA_DIR after import still redirects the log.
        log_file: Log file name (default: mgcp.log)
        max_bytes: Maximum size per log file before rotation (default: 10 MB)
        backup_count: Number of backup files to keep (default: 5)
        log_level: Logging level (default: INFO)
        log_format: Log message format
        console_output: Whether to also log to console (default: True)

    Returns:
        The root mgcp logger instance.

    Note:
        With default settings, maximum disk usage for logs is approximately:
        - 10 MB × 6 files (current + 5 backups) = 60 MB max

        Old logs are automatically deleted when the backup limit is reached.
    """
    global _configured

    # Expand path and create directory. Resolving the default here rather than
    # in the signature is what lets a caller set MGCP_DATA_DIR after import.
    log_path = Path(os.path.expanduser(log_dir if log_dir is not None else _default_log_dir()))
    log_path.mkdir(parents=True, exist_ok=True)
    full_log_path = log_path / log_file

    # Get the root mgcp logger
    root_logger = logging.getLogger("mgcp")
    root_logger.setLevel(log_level)

    # Clear existing handlers to avoid duplicates on reconfiguration
    root_logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(log_format)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        full_log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Console handler (optional)
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Prevent propagation to root logger
    root_logger.propagate = False

    _configured = True

    root_logger.info(
        f"Logging configured: file={full_log_path}, "
        f"max_size={max_bytes // (1024*1024)}MB, "
        f"backups={backup_count}"
    )

    return root_logger
