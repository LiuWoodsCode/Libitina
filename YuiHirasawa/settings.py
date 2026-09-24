"""Persistent settings shared by the Yui shell and its applications."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


CONFIG_PATH = Path.home() / ".config" / "yui" / "config.json"
DEFAULT_CONFIG: dict[str, bool] = {"wallpaper_enabled": True}


def load_config() -> dict[str, bool]:
    """Load the Yui configuration, creating it with defaults when absent."""
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_CONFIG.copy()

    value = data.get("wallpaper_enabled") if isinstance(data, dict) else None
    return {
        "wallpaper_enabled": value
        if isinstance(value, bool)
        else DEFAULT_CONFIG["wallpaper_enabled"]
    }


def save_config(config: Mapping[str, Any]) -> None:
    """Validate and atomically save the complete Yui configuration."""
    wallpaper_enabled_value = config.get(
        "wallpaper_enabled", DEFAULT_CONFIG["wallpaper_enabled"]
    )
    if not isinstance(wallpaper_enabled_value, bool):
        raise ValueError("wallpaper_enabled must be a boolean")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix="config.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(
                {"wallpaper_enabled": wallpaper_enabled_value},
                temporary,
                indent=2,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(CONFIG_PATH)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def wallpaper_enabled() -> bool:
    return load_config()["wallpaper_enabled"]


def set_wallpaper_enabled(enabled: bool) -> None:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    save_config({"wallpaper_enabled": enabled})
