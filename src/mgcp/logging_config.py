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

# Default log configuration
DEFAULT_LOG_DIR = "~/.mgcp/logs"
DEFAULT_LOG_FILE = "mgcp.log"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
DEFAULT_BACKUP_COUNT = 5  # Keep 5 backup files
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

_configured = False


def configure_logging(
    log_dir: str = DEFAULT_LOG_DIR,
    log_file: str = DEFAULT_LOG_FILE,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    log_level: int = DEFAULT_LOG_LEVEL,
    log_format: str = DEFAULT_LOG_FORMAT,
    console_output: bool = True,
) -> logging.Logger:
    """Configure MGCP logging with automatic log rotation.

    Args:
        log_dir: Directory for log files (default: ~/.mgcp/logs)
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

    # Expand path and create directory
    log_path = Path(os.path.expanduser(log_dir))
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


def get_logger(name: str) -> logging.Logger:
    """Get a logger for an MGCP component.

    Args:
        name: Component name (e.g., 'persistence', 'server', 'vector_store')

    Returns:
        A logger instance under the mgcp namespace.

    Example:
        logger = get_logger("persistence")
        logger.info("Database initialized")
        # Logs as: mgcp.persistence - INFO - Database initialized
    """
    global _configured
    if not _configured:
        # Auto-configure with defaults if not already configured
        configure_logging(console_output=False)

    return logging.getLogger(f"mgcp.{name}")


def set_log_level(level: int | str) -> None:
    """Change the log level for all MGCP loggers.

    Args:
        level: Logging level (e.g., logging.DEBUG, "DEBUG", logging.WARNING)
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    root_logger = logging.getLogger("mgcp")
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        handler.setLevel(level)


def cleanup_old_logs(log_dir: str = DEFAULT_LOG_DIR, keep_days: int = 30) -> int:
    """Manually clean up log files older than specified days.

    This is an additional cleanup beyond rotation. Rotation handles size limits,
    but this can be used for time-based cleanup if needed.

    Args:
        log_dir: Directory containing log files
        keep_days: Delete logs older than this many days

    Returns:
        Number of files deleted
    """
    import time

    log_path = Path(os.path.expanduser(log_dir))
    if not log_path.exists():
        return 0

    cutoff_time = time.time() - (keep_days * 24 * 60 * 60)
    deleted = 0

    for log_file in log_path.glob("*.log*"):
        if log_file.stat().st_mtime < cutoff_time:
            log_file.unlink()
            deleted += 1

    return deleted
