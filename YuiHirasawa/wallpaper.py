"""Live, cover-scaled wallpapers and a per-monitor Wayland background utility."""

import logging

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell

from settings import load_config, wallpaper_path


class Wallpaper(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._state = None
        self._source = None
        self.connect("draw", self._draw)
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
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, _widget, context):
        context.set_source_rgb(0, 0, 0)
        context.paint()
        if self._source is None:
            return False
        width, height = self.get_allocated_width(), self.get_allocated_height()
        source_width, source_height = self._source.get_width(), self._source.get_height()
        scale = max(width / source_width, height / source_height)
        context.save()
        context.translate((width - source_width * scale) / 2, (height - source_height * scale) / 2)
        context.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(context, self._source, 0, 0)
        context.paint()
        context.restore()
        return False


class DesktopWallpaper:
    """Own background-layer surfaces, including hotplugged monitors."""
    def __init__(self):
        self._display = Gdk.Display.get_default()
        self._windows = {}
        self._handlers = [self._display.connect(signal, self._monitors_changed)
                          for signal in ("monitor-added", "monitor-removed")]
        self._monitors_changed()

    def _monitors_changed(self, *_args):
        monitors = [self._display.get_monitor(i) for i in range(self._display.get_n_monitors())]
        for monitor in list(self._windows):
            if monitor not in monitors:
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
            window.add(Wallpaper())
            window.show_all()
            self._windows[monitor] = window

    def close(self):
        for handler in self._handlers:
            self._display.disconnect(handler)
        for window in self._windows.values():
            window.destroy()
        self._windows.clear()


def main():
    wallpaper = DesktopWallpaper()
    try:
        Gtk.main()
    finally:
        wallpaper.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
