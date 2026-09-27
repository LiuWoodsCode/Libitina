#!/usr/bin/env python3
"""Install Libitina applications for the current user."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


KINDS = ("Internal", "Production")
ENVIRONMENTS = ("Global", "Phosh", "Yui")


@dataclass(frozen=True)
class Application:
    app_id: str
    source: Path


def data_home() -> Path:
    """Return the current user's XDG data directory."""
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share"


def find_applications(
    root: Path, kinds: set[str], environments: set[str]
) -> tuple[list[Application], list[Path]]:
    """Find complete apps and incomplete app-like directories below *root*."""
    applications: list[Application] = []
    skipped: list[Path] = []

    for kind in KINDS:
        if kind not in kinds:
            continue
        for environment in ENVIRONMENTS:
            if environment not in environments:
                continue
            category = root / kind / environment
            if not category.is_dir():
                continue
            for candidate in sorted(category.iterdir(), key=lambda path: path.name):
                if not candidate.is_dir():
                    continue
                desktop_file = candidate / "app.desktop"
                launcher = candidate / "launch.sh"
                if not desktop_file.is_file() or not launcher.is_file():
                    skipped.append(candidate)
                    continue
                applications.append(Application(candidate.name, candidate))

    duplicates: dict[str, list[Path]] = {}
    for application in applications:
        duplicates.setdefault(application.app_id, []).append(application.source)
    conflicts = {key: paths for key, paths in duplicates.items() if len(paths) > 1}
    if conflicts:
        details = "; ".join(
            f"{app_id}: {', '.join(map(str, paths))}"
            for app_id, paths in sorted(conflicts.items())
        )
        raise ValueError(f"duplicate application IDs ({details})")

    return applications, skipped


def desktop_exec_argument(path: Path) -> str:
    """Quote one absolute path for a Desktop Entry Exec field."""
    value = str(path)
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, f"\\{character}")
    return f'"{value}"'


def desktop_path_value(path: Path) -> str:
    """Escape a path for a Desktop Entry string value."""
    return str(path).replace("\\", "\\\\").replace("\n", "\\n")


def make_desktop_entry(template: str, app_directory: Path) -> str:
    """Add managed launch fields to the template's Desktop Entry section."""
    lines = template.splitlines()
    section_start: int | None = None
    section_end = len(lines)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[Desktop Entry]":
            section_start = index
            break
    if section_start is None:
        raise ValueError("app.desktop has no [Desktop Entry] section")

    for index in range(section_start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_end = index
            break

    body = [
        line
        for line in lines[section_start + 1 : section_end]
        if line.split("=", 1)[0].strip() not in {"Exec", "Path"}
    ]
    while body and not body[-1].strip():
        body.pop()
    body.extend(
        (
            f"Exec={desktop_exec_argument(app_directory / 'launch.sh')}",
            f"Path={desktop_path_value(app_directory)}",
        )
    )
    result = lines[: section_start + 1] + body + lines[section_end:]
    return "\n".join(result) + "\n"


def replace_directory(source: Path, destination: Path) -> None:
    """Copy *source* into place without exposing a partially copied tree."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".libitina-install-", dir=destination.parent))
    staged = staging_root / destination.name
    backup = staging_root / "previous"
    try:
        shutil.copytree(source, staged, symlinks=True)
        launcher = staged / "launch.sh"
        launcher.chmod(stat.S_IMODE(launcher.stat().st_mode) | 0o111)

        if destination.exists() or destination.is_symlink():
            destination.replace(backup)
        staged.replace(destination)
        if backup.exists() or backup.is_symlink():
            if backup.is_dir() and not backup.is_symlink():
                shutil.rmtree(backup)
            else:
                backup.unlink()
    except BaseException:
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def write_desktop_entry(path: Path, contents: str) -> None:
    """Atomically write a user desktop entry."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
        temporary_path.chmod(0o644)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def refresh_applications(application_directory: Path) -> bool:
    """Refresh the user's desktop application database when supported."""
    updater = shutil.which("update-desktop-database")
    if updater is None:
        print(
            "Warning: update-desktop-database was not found; "
            "the apps list may refresh on the next login.",
            file=sys.stderr,
        )
        return False

    try:
        result = subprocess.run(
            [updater, str(application_directory)],
            check=False,
        )
    except OSError as exc:
        print(f"Warning: could not refresh the apps list: {exc}", file=sys.stderr)
        return False
    if result.returncode:
        print(
            "Warning: update-desktop-database exited with status "
            f"{result.returncode}; the apps list may refresh on the next login.",
            file=sys.stderr,
        )
        return False

    print("Refreshed the apps list.")
    return True


def install(application: Application, user_data: Path) -> tuple[Path, Path]:
    """Install one application payload and its generated desktop entry."""
    install_directory = user_data / "libitina" / "applications" / application.app_id
    desktop_path = user_data / "applications" / f"libitina-{application.app_id}.desktop"
    template = (application.source / "app.desktop").read_text(encoding="utf-8")
    desktop_entry = make_desktop_entry(template, install_directory.resolve())
    replace_directory(application.source, install_directory)
    write_desktop_entry(desktop_path, desktop_entry)
    return install_directory, desktop_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        action="append",
        choices=KINDS,
        help="install this kind (repeatable; default: both)",
    )
    parser.add_argument(
        "--environment",
        action="append",
        choices=ENVIRONMENTS,
        help="install this environment (repeatable; default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be installed without changing any files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(__file__).resolve().parent
    kinds = set(args.kind or KINDS)
    environments = set(args.environment or ENVIRONMENTS)

    try:
        applications, skipped = find_applications(root, kinds, environments)
    except ValueError as exc:
        print(f"install: {exc}", file=sys.stderr)
        return 2

    for candidate in skipped:
        missing = [
            name
            for name in ("launch.sh", "app.desktop")
            if not (candidate / name).is_file()
        ]
        print(f"Skipping {candidate.relative_to(root)} (missing {', '.join(missing)})")

    user_data = data_home().resolve()
    installed_count = 0
    for application in applications:
        install_directory = (
            user_data / "libitina" / "applications" / application.app_id
        )
        desktop_path = (
            user_data / "applications" / f"libitina-{application.app_id}.desktop"
        )
        if args.dry_run:
            print(
                f"Would install {application.source.relative_to(root)} to "
                f"{install_directory} and {desktop_path}"
            )
            continue
        try:
            install(application, user_data)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"install: could not install {application.app_id}: {exc}", file=sys.stderr)
            return 1
        print(f"Installed {application.app_id} to {install_directory}")
        print(f"Created {desktop_path}")
        installed_count += 1

    if not applications:
        print("No complete applications found.")
    elif installed_count:
        refresh_applications(user_data / "applications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
