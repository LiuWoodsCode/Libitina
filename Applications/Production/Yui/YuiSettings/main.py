#!/usr/bin/env python3
"""Standalone settings application for Yui."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Handy", "1")
from gi.repository import Gio, GLib, Gtk, Handy

from display_page import DisplayPage
from settings import (
    load_config, set_software_rendering_apps, set_wallpaper_enabled,
    wallpaper_enabled,
)


INTERFACE_SCHEMA = "org.gnome.desktop.interface"
COLOR_SCHEME_KEY = "color-scheme"
GTK_THEME_KEY = "gtk-theme"
ADWAITA_THEME = "Adwaita"
ADWAITA_DARK_THEME = "Adwaita-dark"
WM_SCHEMA = "org.gnome.desktop.wm.preferences"
BUTTON_LAYOUT_KEY = "button-layout"
VISIBLE_BUTTON_LAYOUT = "appmenu:minimize,maximize,close"
HIDDEN_BUTTON_LAYOUT = "appmenu:"


def interface_settings() -> Gio.Settings | None:
    """Return interface settings when the desktop supports theme settings."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        return None
    schema = source.lookup(INTERFACE_SCHEMA, True)
    if schema is None or not (
        schema.has_key(COLOR_SCHEME_KEY) or schema.has_key(GTK_THEME_KEY)
    ):
        return None
    return Gio.Settings.new_full(schema, None, None)


def setting_has_key(settings: Gio.Settings, key: str) -> bool:
    return settings.get_property("settings-schema").has_key(key)


def window_button_settings() -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(WM_SCHEMA, True) if source is not None else None
    if schema is None or not schema.has_key(BUTTON_LAYOUT_KEY):
        return None
    return Gio.Settings.new_full(schema, None, None)


def set_window_button_fix(settings: Gio.Settings, enabled: bool) -> None:
    """Show window controls or hide them using Phosh's button layout."""
    if not settings.is_writable(BUTTON_LAYOUT_KEY):
        raise OSError("The window-button layout is locked by your administrator.")
    layout = VISIBLE_BUTTON_LAYOUT if enabled else HIDDEN_BUTTON_LAYOUT
    if not settings.set_string(BUTTON_LAYOUT_KEY, layout):
        raise OSError("Could not change the window-button layout.")
    Gio.Settings.sync()


def dark_mode_enabled(settings: Gio.Settings) -> bool:
    """Account for both the preference and a manually selected dark theme."""
    prefers_dark = (
        setting_has_key(settings, COLOR_SCHEME_KEY)
        and settings.get_string(COLOR_SCHEME_KEY) == "prefer-dark"
    )
    dark_theme = (
        setting_has_key(settings, GTK_THEME_KEY)
        and settings.get_string(GTK_THEME_KEY).casefold()
        == ADWAITA_DARK_THEME.casefold()
    )
    return prefers_dark or dark_theme


def get_os_name() -> str:
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            values = {}
            for line in os_release.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value.strip().strip('"')
            return values.get("PRETTY_NAME", values.get("NAME", "Unknown"))
        except OSError:
            pass
    return platform.system() or "Unknown"


def make_page() -> Gtk.Box:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    page.set_border_width(24)
    return page


class SettingsWindow(Handy.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application, title="Settings")
        self.set_default_size(760, 460)
        self.set_size_request(320, 240)

        self._interface_settings = interface_settings()
        self._window_button_settings = window_button_settings()
        self._stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.NONE,
        )
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)
        self._add_wallpaper_page()
        self._add_appearance_page()
        self._stack.add_titled(DisplayPage(), "display", "Display")
        self._add_advanced_page()
        self._add_about_page()

        self._sidebar_list = self._make_sidebar()
        self._content_header = Handy.HeaderBar(title="Wallpaper")
        self._content_header.set_show_close_button(True)
        self._back_button = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic", Gtk.IconSize.BUTTON
        )
        self._back_button.set_tooltip_text("Back to Settings")
        self._back_button.get_style_context().add_class("image-button")
        self._back_button.connect("clicked", self._show_sidebar)
        self._back_button.set_no_show_all(True)
        self._back_button.hide()
        self._content_header.pack_start(self._back_button)

        sidebar_header = Handy.HeaderBar(title="Settings")
        sidebar_header.set_show_close_button(True)
        sidebar_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_pane.set_size_request(220, -1)
        sidebar_pane.pack_start(sidebar_header, False, False, 0)
        sidebar_pane.pack_start(self._sidebar_list, True, True, 0)

        content_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_pane.set_size_request(360, -1)
        content_pane.pack_start(self._content_header, False, False, 0)
        content_pane.pack_start(self._stack, True, True, 0)

        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self._leaflet = Handy.Leaflet()
        self._leaflet.set_transition_type(Handy.LeafletTransitionType.SLIDE)
        self._leaflet.set_can_swipe_back(True)
        self._leaflet.add(sidebar_pane)
        self._leaflet.add(separator)
        self._leaflet.add(content_pane)
        self._leaflet.child_set_property(separator, "navigatable", False)
        self._leaflet.set_visible_child(sidebar_pane)
        self._leaflet.connect("notify::folded", self._on_folded_changed)
        self._sidebar_pane = sidebar_pane
        self._content_pane = content_pane

        header_group = Handy.HeaderGroup()
        header_group.add_header_bar(sidebar_header)
        header_group.add_header_bar(self._content_header)
        self._header_group = header_group
        self.add(self._leaflet)

    def _make_sidebar(self) -> Gtk.ListBox:
        sidebar = Gtk.ListBox()
        sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)
        sidebar.set_activate_on_single_click(True)
        sidebar.get_style_context().add_class("navigation-sidebar")
        pages = (
            ("wallpaper", "Wallpaper", "preferences-desktop-wallpaper-symbolic"),
            ("appearance", "Appearance", "preferences-desktop-theme-symbolic"),
            ("display", "Display", "preferences-desktop-display-symbolic"),
            ("advanced", "Advanced", "preferences-system-symbolic"),
            ("about", "About", "help-about-symbolic"),
        )
        first_row = None
        for page_name, title, icon_name in pages:
            row = Gtk.ListBoxRow()
            row.page_name = page_name
            row.page_title = title
            contents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            contents.set_border_width(12)
            contents.pack_start(
                Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU),
                False, False, 0,
            )
            contents.pack_start(Gtk.Label(label=title, xalign=0), True, True, 0)
            row.add(contents)
            sidebar.add(row)
            if first_row is None:
                first_row = row
        sidebar.select_row(first_row)
        sidebar.connect("row-selected", self._on_sidebar_row_selected)
        sidebar.connect("row-activated", self._on_sidebar_row_activated)
        return sidebar

    def _add_wallpaper_page(self) -> None:
        page = make_page()
        checkbox = Gtk.CheckButton(label="Enable wallpaper")
        checkbox.set_active(wallpaper_enabled())
        checkbox.connect("toggled", self._on_wallpaper_toggled)
        page.pack_start(checkbox, False, False, 0)
        self._stack.add_titled(page, "wallpaper", "Wallpaper")

    def _add_appearance_page(self) -> None:
        page = make_page()
        self._dark_mode_checkbox = Gtk.CheckButton(label="Dark mode")
        if self._interface_settings is None:
            self._dark_mode_checkbox.set_sensitive(False)
            self._dark_mode_checkbox.set_tooltip_text(
                "Dark mode is not supported by this desktop"
            )
        else:
            self._dark_mode_checkbox.set_active(
                dark_mode_enabled(self._interface_settings)
            )
            self._dark_mode_checkbox.connect(
                "toggled", self._on_dark_mode_toggled
            )
            for key in (COLOR_SCHEME_KEY, GTK_THEME_KEY):
                if setting_has_key(self._interface_settings, key):
                    self._interface_settings.connect(
                        f"changed::{key}", self._on_dark_mode_setting_changed
                    )
        page.pack_start(self._dark_mode_checkbox, False, False, 0)
        self._stack.add_titled(page, "appearance", "Appearance")

    def _add_advanced_page(self) -> None:
        page = make_page()
        title = Gtk.Label(label="Phosh window-button fix", xalign=0)
        page.pack_start(title, False, False, 0)
        description = Gtk.Label(
            label=(
                "Restore minimize, maximize, and close buttons in GTK apps "
                "after using Phosh. Undo hides these buttons, even if you "
                "have never applied the fix. "
                "This affects both desktops; switching Phosh modes may "
                "overwrite it again. You may need to reopen affected apps."
            ),
            xalign=0,
        )
        description.set_line_wrap(True)
        description.set_max_width_chars(48)
        page.pack_start(description, False, False, 0)
        self._show_window_buttons = Gtk.Button(label="Show window buttons")
        self._undo_window_buttons = Gtk.Button(label="Undo fix")
        for button, enabled in (
            (self._show_window_buttons, True),
            (self._undo_window_buttons, False),
        ):
            button.connect("clicked", self._on_window_button_fix, enabled)
            page.pack_start(button, False, False, 0)
        self._window_button_status = Gtk.Label(xalign=0)
        self._window_button_status.set_line_wrap(True)
        self._window_button_status.set_max_width_chars(48)
        page.pack_start(self._window_button_status, False, False, 0)
        self._refresh_window_button_controls()
        if self._window_button_settings is not None:
            self._window_button_settings.connect(
                f"writable-changed::{BUTTON_LAYOUT_KEY}",
                lambda *_args: self._refresh_window_button_controls(),
            )
        self._add_software_rendering_controls(page)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(page)
        self._stack.add_titled(scroll, "advanced", "Advanced")

    def _add_software_rendering_controls(self, page: Gtk.Box) -> None:
        page.pack_start(Gtk.Separator(), False, False, 0)
        page.pack_start(Gtk.Label(label="Software rendering", xalign=0), False, False, 0)
        description = Gtk.Label(
            label=(
                "Launch these apps with software rendering when opened from Yui. "
                "Use the Desktop ID shown by right-clicking an app and choosing "
                "Details, including .desktop. Changes apply the next time you "
                "launch the app; close any running instance first."
            ),
            xalign=0, wrap=True,
        )
        description.set_max_width_chars(48)
        page.pack_start(description, False, False, 0)
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._software_app_entry = Gtk.Entry()
        self._software_app_entry.set_placeholder_text("org.example.App.desktop")
        self._software_app_entry.set_hexpand(True)
        self._software_app_entry.connect("activate", self._add_software_app)
        entry_row.pack_start(self._software_app_entry, True, True, 0)
        add = Gtk.Button(label="Add")
        add.connect("clicked", self._add_software_app)
        entry_row.pack_end(add, False, False, 0)
        page.pack_start(entry_row, False, False, 0)
        self._software_app_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        page.pack_start(self._software_app_list, False, False, 0)
        self._software_app_status = Gtk.Label(xalign=0, wrap=True)
        self._software_app_status.set_max_width_chars(48)
        page.pack_start(self._software_app_status, False, False, 0)
        self._refresh_software_apps()

    def _refresh_software_apps(self) -> None:
        for row in self._software_app_list.get_children():
            row.destroy()
        app_ids = load_config()["software_rendering_apps"]
        for app_id in app_ids:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin=8)
            label = Gtk.Label(label=app_id, xalign=0, selectable=True, wrap=True)
            label.set_max_width_chars(32)
            row.pack_start(label, True, True, 0)
            remove = Gtk.Button.new_from_icon_name("list-remove-symbolic", Gtk.IconSize.BUTTON)
            remove.set_tooltip_text(f"Remove {app_id}")
            remove.connect("clicked", self._remove_software_app, app_id)
            row.pack_end(remove, False, False, 0)
            self._software_app_list.add(row)
        if not app_ids:
            self._software_app_list.add(Gtk.Label(label="No applications added.", xalign=0, margin=8))
        self._software_app_list.show_all()

    def _save_software_apps(self, app_ids: list[str]) -> bool:
        try:
            set_software_rendering_apps(app_ids)
        except (OSError, ValueError) as exc:
            self._software_app_status.set_text(f"Could not save: {exc}")
            return False
        self._software_app_status.set_text("Saved. Applies to the next launch from Yui.")
        self._refresh_software_apps()
        return True

    def _add_software_app(self, _widget) -> None:
        app_id = self._software_app_entry.get_text().strip()
        if not app_id:
            self._software_app_status.set_text("Enter a desktop ID first.")
            return
        app_ids = load_config()["software_rendering_apps"]
        if app_id in app_ids:
            self._software_app_status.set_text("This application is already in the list.")
            return
        if self._save_software_apps([*app_ids, app_id]):
            self._software_app_entry.set_text("")

    def _remove_software_app(self, _button: Gtk.Button, app_id: str) -> None:
        app_ids = load_config()["software_rendering_apps"]
        self._save_software_apps([item for item in app_ids if item != app_id])

    def _refresh_window_button_controls(self) -> None:
        supported = self._window_button_settings is not None
        writable = supported and self._window_button_settings.is_writable(
            BUTTON_LAYOUT_KEY
        )
        self._show_window_buttons.set_sensitive(writable)
        self._undo_window_buttons.set_sensitive(writable)
        if not supported:
            self._window_button_status.set_text(
                "Window-button settings are not supported by this desktop."
            )
        elif not writable:
            self._window_button_status.set_text(
                "The window-button layout is locked by your administrator."
            )

    def _on_window_button_fix(self, _button: Gtk.Button, enabled: bool) -> None:
        if self._window_button_settings is None:
            return
        try:
            set_window_button_fix(self._window_button_settings, enabled)
        except (OSError, GLib.Error) as exc:
            self._window_button_status.set_text(str(exc))
        else:
            self._window_button_status.set_text(
                "Window buttons enabled." if enabled else "Window buttons hidden."
            )
        self._refresh_window_button_controls()

    def _add_about_page(self) -> None:
        page = make_page()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.pack_start(
            Gtk.Label(label="Operating System", xalign=0), True, True, 0
        )
        value = Gtk.Label(label=get_os_name(), xalign=1)
        value.set_selectable(True)
        value.set_line_wrap(True)
        row.pack_end(value, False, False, 0)
        page.pack_start(row, False, False, 0)
        self._stack.add_titled(page, "about", "About")

    def _on_sidebar_row_selected(
        self, _sidebar: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if row is None:
            return
        self._stack.set_visible_child_name(row.page_name)
        self._content_header.set_title(row.page_title)

    def _on_sidebar_row_activated(
        self, _sidebar: Gtk.ListBox, _row: Gtk.ListBoxRow
    ) -> None:
        self._leaflet.set_visible_child(self._content_pane)

    def _show_sidebar(self, _button: Gtk.Button) -> None:
        self._leaflet.set_visible_child(self._sidebar_pane)

    def _on_folded_changed(self, leaflet: Handy.Leaflet, _pspec) -> None:
        self._back_button.set_visible(leaflet.get_folded())

    def _on_dark_mode_toggled(self, checkbox: Gtk.CheckButton) -> None:
        if self._interface_settings is None:
            return

        enabled = checkbox.get_active()
        if setting_has_key(self._interface_settings, COLOR_SCHEME_KEY):
            self._interface_settings.set_string(
                COLOR_SCHEME_KEY,
                "prefer-dark" if enabled else "default",
            )

        if setting_has_key(self._interface_settings, GTK_THEME_KEY):
            theme = self._interface_settings.get_string(GTK_THEME_KEY)
            if enabled and theme.casefold() == ADWAITA_THEME.casefold():
                self._interface_settings.set_string(
                    GTK_THEME_KEY, ADWAITA_DARK_THEME
                )
            elif (
                not enabled
                and theme.casefold() == ADWAITA_DARK_THEME.casefold()
            ):
                self._interface_settings.set_string(GTK_THEME_KEY, ADWAITA_THEME)

    def _on_dark_mode_setting_changed(
        self, settings: Gio.Settings, _key: str
    ) -> None:
        active = dark_mode_enabled(settings)
        if self._dark_mode_checkbox.get_active() != active:
            self._dark_mode_checkbox.handler_block_by_func(
                self._on_dark_mode_toggled
            )
            self._dark_mode_checkbox.set_active(active)
            self._dark_mode_checkbox.handler_unblock_by_func(
                self._on_dark_mode_toggled
            )

    def _on_wallpaper_toggled(self, checkbox: Gtk.CheckButton) -> None:
        enabled = checkbox.get_active()
        try:
            set_wallpaper_enabled(enabled)
        except OSError as exc:
            checkbox.handler_block_by_func(self._on_wallpaper_toggled)
            checkbox.set_active(not enabled)
            checkbox.handler_unblock_by_func(self._on_wallpaper_toggled)
            dialog = Gtk.MessageDialog(
                transient_for=self, modal=True,
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
    Handy.init()
    return SettingsApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
