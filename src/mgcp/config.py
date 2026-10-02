"""Machine-local MGCP settings that have to outlive a shell.

`MGCP_QDRANT_URL` as an environment variable cannot reach an MCP server the
harness spawns, and that is the case this feature is about. Several agent
sessions each start from a client whose environment you do not control.
A file in the data directory is readable by every one of them, so the setting
survives where an export does not.

A corrupt file raises rather than being ignored. Silently falling back to
embedded while the operator believes they are on the server is the
silent-divergence failure that one resolver for every construction site exists
to prevent: two sessions would write to two different stores and neither would
say so.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("mgcp.config")

CONFIG_FILENAME = "config.json"


def get_data_dir() -> Path:
    """The MGCP data directory, honouring MGCP_DATA_DIR."""
    data_dir = os.environ.get("MGCP_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser()
    return Path("~/.mgcp").expanduser()


def config_path() -> Path:
    """Where machine-local settings live."""
    return get_data_dir() / CONFIG_FILENAME


def load_config() -> dict[str, Any]:
    """Read the config, or {} when there is none.

    Raises ValueError when the file exists and does not parse.
    """
    path = config_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path} is not valid JSON ({exc}). MGCP will not guess which vector "
            "store you meant: fix the file, or delete it to fall back to embedded."
        ) from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a JSON object, found {type(loaded).__name__}.")
    return loaded


def get_value(key: str, default: Any = None) -> Any:
    """One setting, or `default` when unset."""
    return load_config().get(key, default)


def save_config(config: dict[str, Any]) -> Path:
    """Write the whole config atomically.

    Atomic because the PreToolUse hook and every MGCP process may read this
    while it is being written; `open(p, "w")` truncates first, so a reader can
    see an empty file. Same reason enforcement_rules.json is written this way.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    os.replace(tmp, path)
    return path


def set_value(key: str, value: Any) -> Path:
    """Merge one setting into the config, preserving the rest."""
    config = load_config()
    if value is None:
        config.pop(key, None)
    else:
        config[key] = value
    return save_config(config)
