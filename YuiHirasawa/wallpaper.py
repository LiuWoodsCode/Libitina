"""Live, cover-scaled wallpapers and a per-monitor Wayland background utility."""

import logging
import math
import platform
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell

from settings import load_config, wallpaper_path


def internal_watermark_text():
    """Read host and checkout information without relying on the working directory."""
    try:
        pretty_name = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    except OSError:
        pretty_name = "Linux"

    def git_value(*args):
        try:
            return subprocess.check_output(
                ["git", *args], cwd=Path(__file__).resolve().parent,
                stderr=subprocess.DEVNULL, text=True, timeout=2,
            ).strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    branch = git_value("rev-parse", "--abbrev-ref", "HEAD")
    commit = git_value("rev-parse", "HEAD")
    return f"{pretty_name}\n{platform.release()}\nb={branch} c={commit}\nI <3 Jamie Paige"


class Wallpaper(Gtk.Image):
    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._state = None
        self._source = None
        self._scaled_size = None
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.get_style_context().add_class("yui-wallpaper")
        provider = Gtk.CssProvider()
        provider.load_from_data(b".yui-wallpaper { background-color: black; }")
        self.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.connect("size-allocate", self._size_allocate)
        self._timer = GLib.timeout_add_seconds(1, self._sync)
        self.connect("destroy", lambda *_: GLib.source_remove(self._timer))
        self._sync()

    def _sync(self):
        config = load_config()
        state = (config["wallpaper_enabled"], str(wallpaper_path(config)))
        if state != self._state:
            self._state = state
            self._source = None
            if state[0]:
                try:
                    self._source = GdkPixbuf.Pixbuf.new_from_file(state[1])
                except GLib.Error as exc:
                    logging.warning("Could not load wallpaper %s: %s", state[1], exc)
                    # A removed or unreadable custom image must not break the shell.
                    try:
                        self._source = GdkPixbuf.Pixbuf.new_from_file(str(wallpaper_path({"wallpaper": "zohran"})))
                    except GLib.Error:
                        pass
            self._scaled_size = None
            self.clear()
            self._update_image(self.get_allocated_width(), self.get_allocated_height())
        return GLib.SOURCE_CONTINUE

    # A wallpaper fills its allocation; the image's pixel size must never set
    # the window's minimum size (especially after a monitor resolution change).
    def do_get_preferred_width(self):
        return (0, 0)

    def do_get_preferred_height(self):
        return (0, 0)

    def _size_allocate(self, _widget, allocation):
        self._update_image(allocation.width, allocation.height)

    def _update_image(self, width, height):
        if self._source is None or width <= 0 or height <= 0:
            return
        if self._scaled_size == (width, height):
            return
        source_width, source_height = self._source.get_width(), self._source.get_height()
        scale = max(width / source_width, height / source_height)
        scaled_width = max(width, math.ceil(source_width * scale))
        scaled_height = max(height, math.ceil(source_height * scale))
        cover = self._source.scale_simple(scaled_width, scaled_height, GdkPixbuf.InterpType.BILINEAR)
        if cover is None:
            return
        cropped = GdkPixbuf.Pixbuf.new_subpixbuf(
            cover, (scaled_width - width) // 2, (scaled_height - height) // 2,
            width, height,
        )
        self.set_from_pixbuf(cropped)
        self._scaled_size = (width, height)


class DesktopWallpaper:
    """Own background-layer surfaces, including hotplugged monitors."""
    def __init__(self, panel_height=36):
        self._panel_height = panel_height
        self._display = Gdk.Display.get_default()
        self._windows = {}
        self._watermarks = {}
        self._watermark_text = None
        self._handlers = [self._display.connect(signal, self._monitors_changed)
                          for signal in ("monitor-added", "monitor-removed")]
        self._monitors_changed()
        self._timer = GLib.timeout_add_seconds(1, self._sync_watermarks)

    def _sync_watermarks(self):
        enabled = load_config()["show_internal_watermark"]
        if enabled and self._watermark_text is None:
            self._watermark_text = internal_watermark_text()
        for label in self._watermarks.values():
            if enabled:
                label.set_text(self._watermark_text)
            label.set_visible(enabled)
        return GLib.SOURCE_CONTINUE

    def _monitors_changed(self, *_args):
        monitors = [self._display.get_monitor(i) for i in range(self._display.get_n_monitors())]
        for monitor in list(self._windows):
            if monitor not in monitors:
                self._watermarks.pop(monitor)
                self._windows.pop(monitor).destroy()
        for monitor in monitors:
            if monitor in self._windows:
                continue
            window = Gtk.Window(title="Yui Wallpaper")
            window.set_decorated(False)
            GtkLayerShell.init_for_window(window)
            GtkLayerShell.set_monitor(window, monitor)
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.BACKGROUND)
            for edge in (GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT,
                         GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM):
                GtkLayerShell.set_anchor(window, edge, True)
            GtkLayerShell.set_exclusive_zone(window, -1)
            GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_namespace(window, "yui-wallpaper")
            overlay = Gtk.Overlay()
            overlay.add(Wallpaper())
            label = Gtk.Label(xalign=1)
            label.set_justify(Gtk.Justification.RIGHT)
            label.set_halign(Gtk.Align.END)
            label.set_valign(Gtk.Align.END)
            label.set_margin_end(8)
            label.set_margin_bottom(self._panel_height + 8)
            label.set_no_show_all(True)
            provider = Gtk.CssProvider()
            provider.load_from_data(b"label { color: white; font-size: 13px; text-shadow: 0 1px 2px black; }")
            label.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            overlay.add_overlay(label)
            overlay.set_overlay_pass_through(label, True)
            self._watermarks[monitor] = label
            window.add(overlay)
            window.show_all()
            self._windows[monitor] = window
        self._sync_watermarks()

    def close(self):
        GLib.source_remove(self._timer)
        for handler in self._handlers:
            self._display.disconnect(handler)
        for window in self._windows.values():
            window.destroy()
        self._windows.clear()
        self._watermarks.clear()


def main():
    wallpaper = DesktopWallpaper()
    try:
        Gtk.main()
    finally:
        wallpaper.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
