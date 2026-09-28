#!/usr/bin/env python3

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


OS_RELEASE = Path("/etc/os-release")
BACKUP = Path("/etc/os-release-backup")


def parse_os_release(path: Path) -> dict[str, str]:
    """Read an os-release file into a dictionary."""
    values = {}

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            value = value.strip()

            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in ('"', "'")
            ):
                value = value[1:-1]

            values[key] = value

    return values

def get_org_name_for_pretty_name(string):
    if string == "postmarketOS":
        return "Nura"
    elif string == "Nura":
        return "Nura"
    elif string == "Debian GNU/Linux":
        return "Debian"
    else:
        return string

def get_org_name_for_id_name(string):
    if string == "postmarketos":
        return "nura"
    else:
        return string


def get_backup_path() -> Path:
    """
    Return /etc/os-release-backup if it does not exist.

    Otherwise, return a timestamped backup path such as:
    /etc/os-release-backup-2026-09-27T18-50-31Z
    """

    if not BACKUP.exists():
        return BACKUP

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
        .replace(":", "-")
    )

    backup_path = Path(f"{BACKUP}-{timestamp}")

    # Extremely unlikely, but avoid overwriting a backup if this script
    # somehow runs multiple times within the same second.
    counter = 1

    while backup_path.exists():
        backup_path = Path(f"{BACKUP}-{timestamp}-{counter}")
        counter += 1

    return backup_path


def find_latest_backup() -> Path | None:
    """
    Find the newest available os-release backup.

    Timestamped backups are preferred based on modification time.
    """

    backups = []

    if BACKUP.exists():
        backups.append(BACKUP)

    backups.extend(
        path
        for path in Path("/etc").glob("os-release-backup-*")
        if path.is_file()
    )

    if not backups:
        return None

    return max(backups, key=lambda path: path.stat().st_mtime)


def revert():
    """Restore /etc/os-release from the newest available backup."""

    backup = find_latest_backup()

    if backup is None:
        raise SystemExit("No os-release backup was found.")

    shutil.copy2(backup, OS_RELEASE)
    OS_RELEASE.chmod(0o644)

    print(f"Restored {OS_RELEASE} from:")
    print(backup)


def rebrand():
    if not OS_RELEASE.exists():
        raise SystemExit("/etc/os-release does not exist.")

    # Read information from the current OS before replacing the file.
    old = parse_os_release(OS_RELEASE)

    old_pretty_name = old.get("PRETTY_NAME", "")
    old_name = old.get("NAME", "")
    old_version_id = old.get("VERSION_ID", "")
    old_version = old.get("VERSION", old_version_id)
    old_id = old.get("ID", "")
    old_id_like = old.get("ID_LIKE", "")

    # Create a backup every time the script rebrands the installation.
    backup_path = get_backup_path()

    shutil.copy2(OS_RELEASE, backup_path)

    print(f"Backed up existing os-release to:")
    print(backup_path)
    print()

    # ============================================================
    # OS BRANDING
    #
    # This f-string becomes the complete /etc/os-release file.
    #
    # Information from the old file can be inserted anywhere using
    # the variables defined above.
    # ============================================================

    new_os_release = f"""PRETTY_NAME="LibitinaOS ({get_org_name_for_pretty_name(old_name)} {old_version_id})"
NAME="LibitinaOS"
VERSION_ID="{old_version_id}"
VERSION="{old_version}"
ID="libitina-{get_org_name_for_id_name(old_id)}"
ID_LIKE="{old_id} {old_id_like}"
LOGO="libitina-logo"
ANSI_COLOR="1;35"

BASE_PRETTY_NAME={old_pretty_name}
BASE_NAME={old_name}
BASE_VERSION_ID={old_version_id}
BASE_VERSION={old_version}
BASE_ID={old_id}
BASE_ID_LIKE={old_id_like}
"""

    OS_RELEASE.write_text(new_os_release, encoding="utf-8")
    OS_RELEASE.chmod(0o644)

    print("Rebranding complete.")
    print()
    print(new_os_release)


def main():
    parser = argparse.ArgumentParser(
        description="Rebrand an existing Linux installation."
    )

    parser.add_argument(
        "--revert",
        action="store_true",
        help="restore /etc/os-release from the newest backup",
    )

    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("This script must be run as root.")

    if args.revert:
        revert()
    else:
        rebrand()


if __name__ == "__main__":
    main()