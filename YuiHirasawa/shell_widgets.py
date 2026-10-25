"""Shared GTK tray presentation and fixed-size icon rendering."""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell

from trayback import StatusNotifierHost, TRAY_ICON_SIZE, TrayItem
from app_launch import application_details


def round_window_corners(window: Gtk.Window, radius: int = 12) -> None:
    """Let GTK paint an opaque themed background with transparent corners."""
    visual = window.get_screen().get_rgba_visual()
    if visual is not None:
        window.set_visual(visual)
    window.set_app_paintable(False)
    window.get_style_context().add_class("rounded-shell-window")
    provider = Gtk.CssProvider()
    provider.load_from_data(f"""
        .rounded-shell-window {{
            background-image: none;
            background-color: alpha(@theme_bg_color, 1);
            border-radius: {radius}px;
            padding: {radius}px;
        }}
    """.encode())
    window.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def attach_application_menu(button: Gtk.Button, parent: Gtk.Window, app, on_open) -> None:
    """Offer the same Open and Details actions in either launcher layout."""
    menu = Gtk.Menu()
    menu.attach_to_widget(button, None)
    open_item = Gtk.MenuItem(label="Open")
    open_item.connect("activate", on_open, app)
    menu.append(open_item)
    details_item = Gtk.MenuItem(label="Details")
    details_item.connect("activate", lambda *_: show_application_details(parent, app))
    menu.append(details_item)
    menu.show_all()

    def button_press(_button, event):
        if event.button != Gdk.BUTTON_SECONDARY:
            return False
        menu.popup_at_pointer(event)
        return True

    def popup_menu(_button):
        menu.popup_at_widget(
            button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST,
            Gtk.get_current_event(),
        )
        return True

    button.connect("button-press-event", button_press)
    button.connect("popup-menu", popup_menu)


def _prepare_launcher_dialog(dialog: Gtk.Dialog, parent: Gtk.Window, namespace: str) -> None:
    """Keep dialogs visible and focused above the launcher's layer surface."""
    GtkLayerShell.init_for_window(dialog)
    GtkLayerShell.set_layer(dialog, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_namespace(dialog, namespace)
    GtkLayerShell.set_exclusive_zone(dialog, 0)
    GtkLayerShell.set_keyboard_mode(dialog, GtkLayerShell.KeyboardMode.EXCLUSIVE)
    if parent.get_window() is not None:
        monitor = parent.get_display().get_monitor_at_window(parent.get_window())
        GtkLayerShell.set_monitor(dialog, monitor)


def show_application_details(parent: Gtk.Window, app) -> None:
    dialog = Gtk.Dialog(
        title=f"{app.get_display_name()} — Details", transient_for=parent,
        modal=True, destroy_with_parent=True,
    )
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    view = Gtk.TextView(editable=False, cursor_visible=True, monospace=True)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_left_margin(12)
    view.set_right_margin(12)
    view.set_top_margin(12)
    view.set_bottom_margin(12)
    try:
        details = application_details(app)
    except (OSError, GLib.Error) as exc:
        details = f"Could not read application details: {exc}"
    view.get_buffer().set_text(details)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(view)
    dialog.get_content_area().pack_start(scroller, True, True, 0)
    _prepare_launcher_dialog(dialog, parent, "yui-application-details")
    # Fit the details window on the narrow displays used by the mobile shell.
    width, height = 620, 440
    if parent.get_window() is not None:
        monitor = parent.get_display().get_monitor_at_window(parent.get_window())
        geometry = monitor.get_geometry()
        width, height = min(width, geometry.width), min(height, geometry.height)
    dialog.set_default_size(width, height)
    # Layer surfaces renegotiate their natural size when wrapped text reflows.
    # Keep the requested width as a minimum so that reflow cannot collapse it.
    dialog.set_size_request(width, min(200, height))
    dialog.show_all()
    try:
        dialog.run()
    finally:
        dialog.destroy()
    if parent.get_visible():
        parent.present()


def show_error_dialog(parent: Gtk.Window, title: str, detail: str,
                      on_retry=None) -> bool:
    """Keep shell errors above the launcher's layer surface."""
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
    )
    if on_retry is not None:
        dialog.add_button("Try Again in foot", Gtk.ResponseType.APPLY)
    dialog.format_secondary_text(detail)
    _prepare_launcher_dialog(dialog, parent, "yui-error")
    try:
        while dialog.run() == Gtk.ResponseType.APPLY and on_retry is not None:
            try:
                on_retry()
            except (GLib.Error, OSError, ValueError) as exc:
                dialog.format_secondary_text(f"{detail}\n\nCould not retry in foot: {exc}")
            else:
                return True
    finally:
        dialog.destroy()
    if parent.get_visible():
        parent.present()
    return False


def _fixed_icon_image(pixbuf: GdkPixbuf.Pixbuf, size: int) -> Gtk.Image:
    """Return an image whose natural size cannot exceed the requested square."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if width <= 0 or height <= 0:
        image = Gtk.Image()
        image.set_size_request(size, size)
        return image
    scale = size / max(width, height)
    scaled_width = max(1, min(size, round(width * scale)))
    scaled_height = max(1, min(size, round(height * scale)))
    if (width, height) != (scaled_width, scaled_height):
        pixbuf = pixbuf.scale_simple(
            scaled_width, scaled_height, GdkPixbuf.InterpType.BILINEAR
        )
    canvas = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, True, 8, size, size
    )
    canvas.fill(0x00000000)
    pixbuf.copy_area(
        0,
        0,
        scaled_width,
        scaled_height,
        canvas,
        (size - scaled_width) // 2,
        (size - scaled_height) // 2,
    )
    image = Gtk.Image.new_from_pixbuf(canvas)
    image.set_size_request(size, size)
    return image


class SystemTray(Gtk.Box):
    ICON_SIZE = TRAY_ICON_SIZE

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self._buttons: dict[str, Gtk.Button] = {}
        self._items: dict[str, TrayItem] = {}
        self._active_menu = None
        self._host = StatusNotifierHost(self._set_items)

    def _set_items(self, items: tuple[TrayItem, ...]) -> bool:
        self._items = {item.key: item for item in items}
        current = set(self._buttons)
        incoming = set(self._items)
        for key in current - incoming:
            self.remove(self._buttons.pop(key))
        for item in items:
            button = self._buttons.get(item.key)
            if button is None:
                button = Gtk.Button()
                button.get_style_context().add_class("tray-button")
                button.connect("clicked", self._activate, item.key)
                button.connect("button-press-event", self._button_press, item.key)
                button.connect("scroll-event", self._scroll, item.key)
                self._buttons[item.key] = button
                self.pack_start(button, False, False, 0)
            old = button.get_child()
            if old is not None:
                button.remove(old)
            button.add(self._image(item))
            button.set_tooltip_text(item.tooltip)
        self.show_all()
        return GLib.SOURCE_REMOVE

    def _image(self, item: TrayItem) -> Gtk.Image:
        theme = Gtk.IconTheme.get_default()
        if item.icon_theme_path and item.icon_theme_path not in theme.get_search_path():
            theme.append_search_path(item.icon_theme_path)
            theme.rescan_if_needed()
        if item.icon_name and item.icon_theme_path:
            base = Path(item.icon_theme_path) / item.icon_name
            candidates = (
                [base]
                if base.suffix
                else [base.with_suffix(ext) for ext in (".png", ".svg", ".xpm")]
            )
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        str(candidate), self.ICON_SIZE, self.ICON_SIZE, True
                    )
                    return _fixed_icon_image(pixbuf, self.ICON_SIZE)
                except GLib.Error:
                    pass
        if item.icon_name and theme.has_icon(item.icon_name):
            try:
                pixbuf = theme.load_icon(
                    item.icon_name,
                    self.ICON_SIZE,
                    Gtk.IconLookupFlags.FORCE_SIZE,
                )
                return _fixed_icon_image(pixbuf, self.ICON_SIZE)
            except GLib.Error:
                pass
        if item.icon_name and os.path.isfile(item.icon_name):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    item.icon_name, self.ICON_SIZE, self.ICON_SIZE, True
                )
                return _fixed_icon_image(pixbuf, self.ICON_SIZE)
            except GLib.Error:
                pass
        if item.icon_rgba:
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(item.icon_rgba), GdkPixbuf.Colorspace.RGB, True, 8,
                item.icon_width, item.icon_height, item.icon_width * 4,
            )
            return _fixed_icon_image(pixbuf, self.ICON_SIZE)
        try:
            pixbuf = theme.load_icon(
                "image-missing",
                self.ICON_SIZE,
                Gtk.IconLookupFlags.FORCE_SIZE,
            )
            return _fixed_icon_image(pixbuf, self.ICON_SIZE)
        except GLib.Error:
            image = Gtk.Image()
            image.set_size_request(self.ICON_SIZE, self.ICON_SIZE)
            return image

    def _activate(self, _button: Gtk.Button, key: str) -> None:
        item = self._items.get(key)
        if item and item.menu_path and item.item_is_menu:
            self._open_menu(_button, key)
        else:
            self._host.invoke(key, "Activate")

    def _button_press(self, _button: Gtk.Button, event, key: str) -> bool:
        if event.button == Gdk.BUTTON_SECONDARY:
            item = self._items.get(key)
            if item and item.menu_path:
                self._open_menu(_button, key)
            else:
                self._host.invoke(key, "ContextMenu", int(event.x_root), int(event.y_root))
            return True
        if event.button == Gdk.BUTTON_MIDDLE:
            self._host.invoke(key, "SecondaryActivate", int(event.x_root), int(event.y_root))
            return True
        return False

    def _scroll(self, _button: Gtk.Button, event, key: str) -> bool:
        if event.direction == Gdk.ScrollDirection.UP:
            delta, orientation = 1, "vertical"
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta, orientation = -1, "vertical"
        elif event.direction == Gdk.ScrollDirection.LEFT:
            delta, orientation = 1, "horizontal"
        elif event.direction == Gdk.ScrollDirection.RIGHT:
            delta, orientation = -1, "horizontal"
        else:
            return False
        self._host.scroll(key, delta, orientation)
        return True

    def _open_menu(self, _button: Gtk.Button, key: str) -> None:
        self._host.request_menu(key, self._show_menu)

    def _show_menu(self, key: str, layout) -> bool:
        button = self._buttons.get(key)
        if button is None or not layout:
            return GLib.SOURCE_REMOVE
        menu = Gtk.Menu()
        self._append_menu_items(menu, layout.get("children", ()), key)
        if not menu.get_children():
            empty = Gtk.MenuItem.new_with_label("No actions")
            empty.set_sensitive(False)
            menu.append(empty)
        menu.show_all()
        self._active_menu = menu
        menu.connect("deactivate", self._menu_closed)
        event = Gtk.get_current_event()
        try:
            menu.popup_at_widget(
                button,
                Gdk.Gravity.NORTH_WEST,
                Gdk.Gravity.SOUTH_WEST,
                event,
            )
        except AttributeError:
            menu.popup(None, None, None, None, 0, Gtk.get_current_event_time())
        return GLib.SOURCE_REMOVE

    def _append_menu_items(self, menu: Gtk.Menu, nodes, key: str) -> None:
        for node in nodes:
            properties = node.get("properties", {})
            if properties.get("type") == "separator":
                widget = Gtk.SeparatorMenuItem()
            else:
                label = str(properties.get("label") or "")
                toggle_type = str(properties.get("toggle-type") or "")
                if toggle_type in ("checkmark", "radio"):
                    widget = Gtk.CheckMenuItem.new_with_mnemonic(label)
                    widget.set_active(int(properties.get("toggle-state", 0)) == 1)
                    widget.set_draw_as_radio(toggle_type == "radio")
                elif properties.get("icon-name") or properties.get("icon-data"):
                    widget = Gtk.ImageMenuItem.new_with_mnemonic(label)
                    image = self._menu_image(properties)
                    if image is not None:
                        widget.set_image(image)
                        widget.set_always_show_image(True)
                else:
                    widget = Gtk.MenuItem.new_with_mnemonic(label)

                children = node.get("children", ())
                if children:
                    submenu = Gtk.Menu()
                    self._append_menu_items(submenu, children, key)
                    widget.set_submenu(submenu)
                else:
                    widget.connect(
                        "activate", self._menu_item_activated, key, int(node["id"])
                    )
                widget.set_sensitive(bool(properties.get("enabled", True)))

            if not bool(properties.get("visible", True)):
                widget.set_no_show_all(True)
                widget.hide()
            menu.append(widget)

    def _menu_image(self, properties):
        icon_name = str(properties.get("icon-name") or "")
        if icon_name:
            if os.path.isfile(icon_name):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        icon_name, 16, 16, True
                    )
                    return Gtk.Image.new_from_pixbuf(pixbuf)
                except GLib.Error:
                    pass
            if Gtk.IconTheme.get_default().has_icon(icon_name):
                return Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        icon_data = properties.get("icon-data")
        if icon_data:
            try:
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(bytes(icon_data))
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf is not None:
                    pixbuf = pixbuf.scale_simple(16, 16, GdkPixbuf.InterpType.BILINEAR)
                    return Gtk.Image.new_from_pixbuf(pixbuf)
            except (GLib.Error, TypeError, ValueError):
                pass
        return None

    def _menu_item_activated(
            self, _menu_item: Gtk.MenuItem, key: str, item_id: int) -> None:
        self._host.menu_event(key, item_id, Gtk.get_current_event_time())

    def _menu_closed(self, _menu: Gtk.Menu) -> None:
        self._active_menu = None


