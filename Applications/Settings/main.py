#!/usr/bin/env python3
"""Standalone settings application for Yui."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, Gtk


CONFIG_PATH = Path.home() / ".config" / "yui" / "config.json"
DEFAULT_WALLPAPER_ENABLED = True

PANELS = (
    ("wifi", "Wi-Fi & Network", "network-wireless-symbolic", "Connect to Wi-Fi and manage network preferences."),
    ("bluetooth", "Bluetooth", "bluetooth-symbolic", "Pair accessories and manage nearby devices."),
    ("cellular", "Cellular", "network-cellular-signal-excellent-symbolic", "Manage mobile data, SIMs, and roaming."),
    ("sound", "Sound", "audio-volume-high-symbolic", "Adjust volume, vibration, and notification sounds."),
    ("display", "Display", "video-display-symbolic", "Set brightness, text size, and screen timeout."),
    ("wallpapers", "Wallpapers", "preferences-desktop-wallpaper-symbolic", "Choose what appears behind your apps."),
    ("battery", "Battery", "battery-good-symbolic", "Review battery use and power-saving options."),
    ("apps", "Apps", "application-x-executable-symbolic", "Manage installed apps, permissions, and defaults."),
    ("privacy", "Privacy & Security", "security-high-symbolic", "Control privacy, location, and device security."),
    ("accessibility", "Accessibility", "preferences-desktop-accessibility-symbolic", "Make Yui easier to see, hear, and use."),
    ("system", "System", "preferences-system-symbolic", "Updates, language, date, backup, and device details."),
)

MENU_GROUPS = (
    ("Connections", ("wifi", "bluetooth", "cellular")),
    ("Device", ("sound", "display", "battery", "apps")),
    ("Personal", ("wallpapers", "privacy", "accessibility")),
    ("System", ("system",)),
)


def wallpaper_enabled() -> bool:
    """Read the one supported setting, using the default for invalid files."""
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
    """Write the complete configuration atomically, without a Yui dependency."""
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
            json.dump({"wallpaper_enabled": bool(enabled)}, temporary, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(CONFIG_PATH)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application, title="Settings")
        self.set_default_size(760, 540)
        self.set_size_request(420, 360)
        self._install_css()

        self._panel_details = {
            panel_id: (title, icon, description)
            for panel_id, title, icon, description in PANELS
        }
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(160)
        for panel_id, title, icon, description in PANELS:
            page = (
                self._wallpapers_page(title, icon, description)
                if panel_id == "wallpapers"
                else self._placeholder_page(title, icon, description)
            )
            self._stack.add_named(page, panel_id)

        header = Gtk.HeaderBar(title="Settings")
        header.set_show_close_button(True)
        header.pack_start(self._build_panel_menu())
        self.set_titlebar(header)
        self.add(self._stack)
        self._show_panel("wifi")

    def _build_panel_menu(self) -> Gtk.MenuButton:
        button = Gtk.MenuButton()
        button.set_tooltip_text("Choose a settings page")
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        content.pack_start(
            Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON),
            False,
            False,
            0,
        )
        self._menu_label = Gtk.Label(label="Wi-Fi & Network")
        content.pack_start(self._menu_label, False, False, 0)
        button.add(content)

        menu = Gtk.Menu()
        for group_title, panel_ids in MENU_GROUPS:
            group_item = Gtk.MenuItem(label=group_title)
            submenu = Gtk.Menu()
            for panel_id in panel_ids:
                title, icon_name, _description = self._panel_details[panel_id]
                item = Gtk.ImageMenuItem(label=title)
                item.set_image(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU))
                item.set_always_show_image(True)
                item.connect("activate", self._on_menu_item_activated, panel_id)
                submenu.append(item)
            group_item.set_submenu(submenu)
            menu.append(group_item)
        menu.show_all()
        button.set_popup(menu)
        return button

    def _page_shell(self, title: str, icon_name: str, description: str) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_border_width(28)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        heading.pack_start(
            Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG),
            False,
            False,
            0,
        )
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("page-title")
        words.pack_start(title_label, False, False, 0)
        description_label = Gtk.Label(label=description, xalign=0)
        description_label.set_line_wrap(True)
        description_label.get_style_context().add_class("dim-label")
        words.pack_start(description_label, False, False, 0)
        heading.pack_start(words, True, True, 0)
        page.pack_start(heading, False, False, 0)
        return page

    def _placeholder_page(self, title: str, icon: str, description: str) -> Gtk.Widget:
        page = self._page_shell(title, icon, description)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("settings-card")
        card.set_border_width(18)
        label = Gtk.Label(label="Coming soon", xalign=0)
        label.get_style_context().add_class("card-title")
        card.pack_start(label, False, False, 0)
        detail = Gtk.Label(
            label="This section is a placeholder while its system integration is being built.",
            xalign=0,
        )
        detail.set_line_wrap(True)
        detail.get_style_context().add_class("dim-label")
        card.pack_start(detail, False, False, 0)
        page.pack_start(card, False, False, 0)
        return page

    def _wallpapers_page(self, title: str, icon: str, description: str) -> Gtk.Widget:
        page = self._page_shell(title, icon, description)

        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        preview.set_size_request(-1, 180)
        preview.set_valign(Gtk.Align.CENTER)
        preview.get_style_context().add_class("wallpaper-preview")
        image = Gtk.Image.new_from_icon_name(
            "preferences-desktop-wallpaper-symbolic", Gtk.IconSize.DIALOG
        )
        image.set_pixel_size(72)
        preview.pack_start(image, True, False, 0)
        preview.pack_start(
            Gtk.Label(label="Bundled placeholder wallpaper"), False, False, 0
        )
        page.pack_start(preview, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.get_style_context().add_class("settings-card")
        row.set_border_width(18)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        name = Gtk.Label(label="Placeholder wallpaper", xalign=0)
        name.get_style_context().add_class("card-title")
        words.pack_start(name, False, False, 0)
        detail = Gtk.Label(
            label="Show the bundled wallpaper on the Yui home screen.", xalign=0
        )
        detail.set_line_wrap(True)
        detail.get_style_context().add_class("dim-label")
        words.pack_start(detail, False, False, 0)
        row.pack_start(words, True, True, 0)

        self._wallpaper_checkbox = Gtk.CheckButton(label="Enabled")
        self._wallpaper_checkbox.set_active(wallpaper_enabled())
        self._wallpaper_checkbox.set_valign(Gtk.Align.CENTER)
        self._wallpaper_checkbox.connect("toggled", self._on_wallpaper_toggled)
        row.pack_end(self._wallpaper_checkbox, False, False, 0)
        page.pack_start(row, False, False, 0)

        config_hint = Gtk.Label(label=f"Saved to {CONFIG_PATH}", xalign=0)
        config_hint.set_selectable(True)
        config_hint.get_style_context().add_class("dim-label")
        page.pack_start(config_hint, False, False, 0)
        return page

    def _on_menu_item_activated(self, _item: Gtk.MenuItem, panel_id: str) -> None:
        self._show_panel(panel_id)

    def _show_panel(self, panel_id: str) -> None:
        self._stack.set_visible_child_name(panel_id)
        self._menu_label.set_text(self._panel_details[panel_id][0])

    def _on_wallpaper_toggled(self, checkbox: Gtk.CheckButton) -> None:
        enabled = checkbox.get_active()
        try:
            set_wallpaper_enabled(enabled)
        except OSError as exc:
            checkbox.handler_block_by_func(self._on_wallpaper_toggled)
            checkbox.set_active(not enabled)
            checkbox.handler_unblock_by_func(self._on_wallpaper_toggled)
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

    @staticmethod
    def _install_css() -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
            .page-title { font-size: 26px; font-weight: bold; }
            .card-title { font-size: 16px; font-weight: bold; }
            .dim-label { opacity: 0.68; }
            .settings-card, .wallpaper-preview {
                border: 1px solid alpha(currentColor, 0.16);
                border-radius: 10px;
                background-color: alpha(currentColor, 0.035);
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


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
