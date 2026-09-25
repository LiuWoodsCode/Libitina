#!/usr/bin/env python3
"""Start the configured Libitina desktop services.

Settings are loaded from ~/.config/libitina/boot_settings.json.  Every setting
is optional; omitted settings use the defaults in DEFAULT_SETTINGS below.  A
complete settings file looks like this::

    {
      "services": ["main", "notifier", "osk"],
      "scale_screen": true,
      "output": "DSI-1",
      "scale": 3,
      "osk": "squeekboard"
    }

The ``osk`` setting is a shell-style command string, so arguments can be
included alongside the executable.  Service names are the stable public names
above rather than filenames or executable names.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "services": ["main", "notifier", "osk"],
    "scale_screen": True,
    "output": "DSI-1",
    "scale": 3,
    "osk": "/home/dapixelprowler/stevia/build/run --replace",
}

VALID_SERVICES = {"main", "notifier", "osk"}

def load_settings(path: Path) -> dict[str, Any]:
    """Load *path* and merge it over the built-in defaults."""
    settings = DEFAULT_SETTINGS.copy()

    if not path.exists():
        return settings

    try:
        with path.open(encoding="utf-8") as settings_file:
            overrides = json.load(settings_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc

    if not isinstance(overrides, dict):
        raise ValueError(f"{path} must contain a JSON object")

    settings.update(overrides)
    validate_settings(settings, path)
    return settings


def validate_settings(settings: dict[str, Any], path: Path) -> None:
    services = settings.get("services")
    if not isinstance(services, list) or not all(
        isinstance(service, str) for service in services
    ):
        raise ValueError(f"'services' in {path} must be a list of names")

    unknown_services = set(services) - VALID_SERVICES
    if unknown_services:
        names = ", ".join(sorted(unknown_services))
        raise ValueError(f"unknown service(s) in {path}: {names}")

    if not isinstance(settings.get("scale_screen"), bool):
        raise ValueError(f"'scale_screen' in {path} must be true or false")

    output = settings.get("output")
    if not isinstance(output, str) or not output:
        raise ValueError(f"'output' in {path} must be a non-empty string")

    scale = settings.get("scale")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale <= 0:
        raise ValueError(f"'scale' in {path} must be a positive number")

    osk = settings.get("osk")
    if not isinstance(osk, str) or not osk.strip():
        raise ValueError(f"'osk' in {path} must be a non-empty command string")

    try:
        shlex.split(osk)
    except ValueError as exc:
        raise ValueError(f"invalid 'osk' command in {path}: {exc}") from exc

def service_commands(settings: dict[str, Any], project_root: Path) -> list[list[str]]:
    """Build the commands selected by the service names in the settings."""
    python = sys.executable
    spawner = project_root / "Spawner" / "main.py"
    yui = project_root / "YuiHirasawa"
    commands = {
        "main": [python, str(spawner), python, str(yui / "main.py")],
        "notifier": [
            python,
            str(spawner),
            python,
            str(yui / "notification.py"),
        ],
        "osk": [python, str(spawner), *shlex.split(settings["osk"])],
    }
    return [commands[name] for name in settings["services"]]


def format_command(command: list[str]) -> str:
    return shlex.join(command)


def start(settings: dict[str, Any], project_root: Path, dry_run: bool) -> int:
    if settings["scale_screen"]:
        scale_command = [
            "wlr-randr",
            "--output",
            settings["output"],
            "--scale",
            str(settings["scale"]),
        ]
        if dry_run:
            print(format_command(scale_command))
        else:
            try:
                result = subprocess.run(scale_command, check=False)
                if result.returncode:
                    print(
                        f"autostart: screen scaling exited with status "
                        f"{result.returncode}; continuing",
                        file=sys.stderr,
                    )
            except OSError as exc:
                print(f"autostart: could not scale the screen: {exc}", file=sys.stderr)

    for command in service_commands(settings, project_root):
        if dry_run:
            print(format_command(command))
            continue

        try:
            subprocess.Popen(command, start_new_session=True)
        except OSError as exc:
            print(
                f"autostart: could not start {format_command(command)}: {exc}",
                file=sys.stderr,
            )
            return 1

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".config" / "libitina" / "boot_settings.json",
        help="settings file (default: ~/.config/libitina/boot_settings.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands without starting them",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent

    try:
        settings = load_settings(args.config.expanduser())
    except ValueError as exc:
        print(f"autostart: {exc}", file=sys.stderr)
        return 2

    return start(settings, project_root, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
