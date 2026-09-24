#!/usr/bin/env python3
"""Standalone settings application for Yui."""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk


CONFIG_PATH = Path.home() / ".config" / "yui" / "config.json"
DEFAULT_WALLPAPER_ENABLED = True


def wallpaper_enabled() -> bool:
    """Read the wallpaper setting, using the default for invalid files."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        set_wallpaper_enabled(DEFAULT_WALLPAPER_ENABLED)
        return DEFAULT_WALLPAPER_ENABLED
    except (OSError, json.JSONDecodeError):
        return DEFAULT_WALLPAPER_ENABLED

    value = data.get("wallpaper_enabled") if isinstance(data, dict) else None
    return value if isinstance(value, bool) else DEFAULT_WALLPAPER_ENABLED


def set_wallpaper_enabled(enabled: bool) -> None:
    """Write the configuration atomically."""
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
                {"wallpaper_enabled": bool(enabled)},
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


def get_os_name() -> str:
    """Return a simple human-readable OS name."""
    os_release = Path("/etc/os-release")

    if os_release.exists():
        try:
            values = {}

            for line in os_release.read_text(encoding="utf-8").splitlines():
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')

            if "PRETTY_NAME" in values:
                return values["PRETTY_NAME"]

            if "NAME" in values:
                return values["NAME"]
        except OSError:
            pass

    return platform.system() or "Unknown"


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application, title="Settings")

        # Let the window manager/compositor provide decorations instead of
        # GTK drawing its own client-side titlebar.
        self.set_decorated(False)

        self.set_default_size(480, 260)
        self.set_size_request(360, 200)

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
        )
        root.set_border_width(24)

        #
        # Wallpapers
        #

        wallpaper_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
        )

        wallpaper_heading = Gtk.Label(
            label="Wallpapers",
            xalign=0,
        )
        wallpaper_heading.set_markup("<b>Wallpapers</b>")
        wallpaper_section.pack_start(
            wallpaper_heading,
            False,
            False,
            0,
        )

        self._wallpaper_checkbox = Gtk.CheckButton(
            label="Enable wallpaper"
        )
        self._wallpaper_checkbox.set_active(wallpaper_enabled())
        self._wallpaper_checkbox.connect(
            "toggled",
            self._on_wallpaper_toggled,
        )

        wallpaper_section.pack_start(
            self._wallpaper_checkbox,
            False,
            False,
            0,
        )

        root.pack_start(
            wallpaper_section,
            False,
            False,
            0,
        )

        #
        # System information
        #

        system_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
        )

        system_heading = Gtk.Label(
            label="System Information",
            xalign=0,
        )
        system_heading.set_markup("<b>System Information</b>")
        system_section.pack_start(
            system_heading,
            False,
            False,
            0,
        )

        os_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=16,
        )

        os_label = Gtk.Label(
            label="Operating System",
            xalign=0,
        )
        os_row.pack_start(os_label, True, True, 0)

        os_value = Gtk.Label(
            label=get_os_name(),
            xalign=1,
        )
        os_value.set_selectable(True)
        os_row.pack_end(os_value, False, False, 0)

        system_section.pack_start(
            os_row,
            False,
            False,
            0,
        )

        root.pack_start(
            system_section,
            False,
            False,
            0,
        )

        self.add(root)

    def _on_wallpaper_toggled(
        self,
        checkbox: Gtk.CheckButton,
    ) -> None:
        enabled = checkbox.get_active()

        try:
            set_wallpaper_enabled(enabled)
        except OSError as exc:
            checkbox.handler_block_by_func(
                self._on_wallpaper_toggled
            )
            checkbox.set_active(not enabled)
            checkbox.handler_unblock_by_func(
                self._on_wallpaper_toggled
            )

            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="Could not save the wallpaper setting.",
            )
            dialog.format_secondary_text(str(exc))
            dialog.run()
            dialog.destroy()


class SettingsApplication(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="org.libitina.Yui.Settings",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

    def do_activate(self) -> None:
        window = self.get_active_window()

        if window is None:
            window = SettingsWindow(self)

        window.show_all()
        window.present()


def main() -> int:
    return SettingsApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())