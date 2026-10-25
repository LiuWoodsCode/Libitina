"""Persistent settings shared by the Yui shell and its applications."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


CONFIG_PATH = Path.home() / ".config" / "yui" / "config.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "wallpaper": "zohran", "wallpaper_enabled": True, "shell": "desktop", "software_rendering_apps": [],
    "squeekboard_enabled": False,
}
WALLPAPERS = {"zohran": "zohran.png", "shibuya": "shibuya.jpg"}


def validate_wallpaper(value: str) -> str:
    if not isinstance(value, str) or not value or (value not in WALLPAPERS and not Path(value).is_absolute()):
        raise ValueError("Choose a default wallpaper or an absolute image path.")
    return value


def wallpaper_path(config=None) -> Path:
    value = (load_config() if config is None else config)["wallpaper"]
    if value in WALLPAPERS:
        return Path(__file__).resolve().parent / "assets/icons/backgrounds" / WALLPAPERS[value]
    return Path(value)


def set_wallpaper(value: str) -> None:
    value = validate_wallpaper(value)
    config = load_config()
    config["wallpaper"] = value
    save_config(config)


SHELL_OPTIONS = ("desktop", "mobile")


def validate_software_rendering_apps(app_ids: list[str]) -> list[str]:
    """Validate exact desktop IDs and remove duplicates, retaining their order."""
    if not isinstance(app_ids, list) or any(
        not isinstance(app_id, str)
        or not app_id.endswith(".desktop")
        or app_id == ".desktop"
        or "/" in app_id
        or any(character.isspace() for character in app_id)
        for app_id in app_ids
    ):
        raise ValueError("Use desktop IDs such as org.example.App.desktop, without paths or spaces.")
    return list(dict.fromkeys(app_ids))


def load_config() -> dict[str, Any]:
    """Load the Yui configuration, creating it with defaults when absent."""
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return {**DEFAULT_CONFIG, "software_rendering_apps": []}

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**DEFAULT_CONFIG, "software_rendering_apps": []}

    data = data if isinstance(data, dict) else {}
    try:
        wallpaper = validate_wallpaper(data.get("wallpaper", "zohran"))
    except ValueError:
        wallpaper = "zohran"
    value = data.get("wallpaper_enabled")
    shell = data.get("shell")
    app_ids = data.get("software_rendering_apps", [])
    try:
        app_ids = validate_software_rendering_apps(app_ids)
    except ValueError:
        app_ids = []
    return {
        **data,
        "wallpaper": wallpaper,
        "wallpaper_enabled": value
        if isinstance(value, bool)
        else DEFAULT_CONFIG["wallpaper_enabled"],
        "shell": shell if shell in SHELL_OPTIONS else DEFAULT_CONFIG["shell"],
        "squeekboard_enabled": data.get("squeekboard_enabled", False)
        if isinstance(data.get("squeekboard_enabled", False), bool) else False,
        "software_rendering_apps": app_ids,
    }


def save_config(config: Mapping[str, Any]) -> None:
    """Validate and atomically save the complete Yui configuration."""
    wallpaper_enabled_value = config.get(
        "wallpaper_enabled", DEFAULT_CONFIG["wallpaper_enabled"]
    )
    if not isinstance(wallpaper_enabled_value, bool):
        raise ValueError("wallpaper_enabled must be a boolean")
    squeekboard_enabled = config.get("squeekboard_enabled", False)
    if not isinstance(squeekboard_enabled, bool):
        raise ValueError("squeekboard_enabled must be a boolean")
    wallpaper = validate_wallpaper(config.get("wallpaper", "zohran"))
    shell = config.get("shell", DEFAULT_CONFIG["shell"])
    if shell not in SHELL_OPTIONS:
        raise ValueError("shell must be 'desktop' or 'mobile'")
    app_ids = validate_software_rendering_apps(config.get("software_rendering_apps", []))

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
                {**config, "wallpaper": wallpaper, "wallpaper_enabled": wallpaper_enabled_value,
                 "shell": shell, "software_rendering_apps": app_ids,
                 "squeekboard_enabled": squeekboard_enabled},
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
    config = load_config()
    config["wallpaper_enabled"] = enabled
    save_config(config)


def set_shell(shell: str) -> None:
    """Choose the shell to launch next time Yui starts."""
    if shell not in SHELL_OPTIONS:
        raise ValueError("shell must be 'desktop' or 'mobile'")
    config = load_config()
    config["shell"] = shell
    save_config(config)


def set_software_rendering_apps(app_ids: list[str]) -> None:
    """Choose applications that Yui should launch using software rendering."""
    app_ids = validate_software_rendering_apps(app_ids)
    config = load_config()
    config["software_rendering_apps"] = app_ids
    save_config(config)
