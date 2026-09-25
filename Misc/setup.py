#!/usr/bin/env python3
"""Configure the current user's GTK and Labwc session for Yui."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
import sys
import tempfile
from pathlib import Path


GTK_SECTION = "Settings"
GTK_KEY = "gtk-decoration-layout"
GTK_VALUE = ":"
AUTOSTART_BEGIN = "# BEGIN Yui shell (managed by Misc/setup.py)"
AUTOSTART_END = "# END Yui shell (managed by Misc/setup.py)"


def config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config"


def atomic_write(path: Path, contents: str, new_mode: int) -> None:
    """Atomically replace *path*, retaining its permissions when it exists."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else new_mode
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)

        temporary_path.chmod(mode)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def update_gtk_settings(contents: str) -> str:
    """Set the decoration layout while preserving unrelated key-file data."""
    lines = contents.splitlines()
    section_pattern = re.compile(r"^\s*\[([^]]+)]\s*$")
    key_pattern = re.compile(rf"^\s*{re.escape(GTK_KEY)}\s*=")
    settings_start: int | None = None
    settings_end = len(lines)

    for index, line in enumerate(lines):
        match = section_pattern.match(line)
        if match is None:
            continue
        if settings_start is not None:
            settings_end = index
            break
        if match.group(1).strip().casefold() == GTK_SECTION.casefold():
            settings_start = index

    setting = f"{GTK_KEY}={GTK_VALUE}"
    if settings_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((f"[{GTK_SECTION}]", setting))
    else:
        for index in range(settings_start + 1, settings_end):
            if key_pattern.match(lines[index]):
                lines[index] = setting
                break
        else:
            insertion_point = settings_end
            while (
                insertion_point > settings_start + 1
                and not lines[insertion_point - 1].strip()
            ):
                insertion_point -= 1
            lines.insert(insertion_point, setting)

    return "\n".join(lines) + "\n"


def update_labwc_autostart(contents: str, yui_command: str) -> str:
    """Add or refresh the setup-managed Yui block."""
    block = f"{AUTOSTART_BEGIN}\n{yui_command}\n{AUTOSTART_END}"
    pattern = re.compile(
        rf"(?ms)^{re.escape(AUTOSTART_BEGIN)}\n.*?"
        rf"^{re.escape(AUTOSTART_END)}(?:\n|$)"
    )

    if pattern.search(contents):
        updated = pattern.sub(block + "\n", contents, count=1)
        return updated.rstrip("\n") + "\n"

    updated = contents.rstrip("\n")
    if updated:
        updated += "\n\n"
    elif not contents:
        updated = "#!/bin/sh\n\n"
    return updated + block + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gtk-only",
        action="store_true",
        help="configure GTK title-bar buttons without changing Labwc autostart",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    user_config = config_home()
    for toolkit_version in ("gtk-3.0", "gtk-4.0"):
        settings_path = user_config / toolkit_version / "settings.ini"
        try:
            existing = settings_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = ""
        updated = update_gtk_settings(existing)
        atomic_write(settings_path, updated, 0o600)
        print(f"Configured {settings_path}")

    if args.gtk_only:
        return 0

    project_root = Path(__file__).resolve().parent.parent
    yui_autostart = project_root / "Misc" / "autostart.py"
    labwc_path = user_config / "labwc" / "autostart"
    try:
        existing = labwc_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""

    command = f"python3 {shlex.quote(str(yui_autostart))} &"
    updated = update_labwc_autostart(existing, command)
    atomic_write(labwc_path, updated, 0o700)
    print(f"Configured {labwc_path}")
    print("Yui will start with the next Labwc session.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as exc:
        print(f"setup: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
