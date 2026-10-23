#!/usr/bin/env python3
"""Register the YuiHirasawa login session without changing other sessions."""

import argparse
from pathlib import Path


def quote_exec(value):
    # Desktop Entry Exec quoting, followed by key-file string escaping.
    if any(char in value for char in ('\n', '\r', '\x00')):
        raise ValueError("Session paths must not contain control characters")
    escaped = ''.join('\\' + char if char in '\\"`$' else char for char in value)
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'


def desktop_entry():
    wrapper = Path(__file__).resolve().with_name("launcher.py")
    return (
        "[Desktop Entry]\n"
        "Name=YuiHirasawa\n"
        "Comment=Yui shell on Labwc\n"
        f"Exec=/usr/bin/python3 {quote_exec(str(wrapper))} --session\n"
        "TryExec=labwc\n"
        "Type=Application\n"
        "DesktopNames=labwc;\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, default=Path("/usr/share/wayland-sessions"),
                        help="login manager's Wayland session directory")
    args = parser.parse_args()
    args.session_dir.mkdir(parents=True, exist_ok=True)
    target = args.session_dir / "yuihirasawa.desktop"
    target.write_text(desktop_entry(), encoding="utf-8")
    target.chmod(0o644)
    print(f"Installed {target}. Select YuiHirasawa at login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
