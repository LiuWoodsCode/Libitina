"""Shared GTK 3 UI and asynchronous D-Bus transport for internal playgrounds."""

import datetime
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk


class Playground(Gtk.ApplicationWindow):
    def __init__(self, app, title, service, path):
        super().__init__(application=app, title=title)
        self.service, self.path = service, path
        self.connection = None
        self.subscription = None
        self.cancellable = Gio.Cancellable()
        self.set_default_size(380, 720)
        header = Gtk.HeaderBar(title=title, show_close_button=True)
        self.set_titlebar(header)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.add(scroll)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.body.set_border_width(12)
        scroll.add(self.body)
        self.log_view = Gtk.TextView(editable=False, cursor_visible=False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(180)
        log_scroll.add(self.log_view)
        self.log_scroll = log_scroll
        self.connect("destroy", self.cleanup)

    def field(self, title, default=""):
        label = Gtk.Label(label=title, xalign=0)
        label.set_line_wrap(True)
        self.body.pack_start(label, False, False, 0)
        entry = Gtk.Entry(text=default)
        entry.set_width_chars(12)
        self.body.pack_start(entry, False, False, 0)
        return entry

    def button(self, title, callback):
        button = Gtk.Button(label=title)
        button.connect("clicked", lambda *_: self.guard(callback))
        self.body.pack_start(button, False, False, 0)
        return button

    def guard(self, callback):
        try:
            callback()
        except (ValueError, TypeError, OverflowError, GLib.Error) as error:
            self.log(f"Error: {error}")

    def log(self, message):
        buffer = self.log_view.get_buffer()
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        buffer.insert(buffer.get_end_iter(), f"{stamp} {message}\n")
        # Bound memory during long testing sessions.
        if buffer.get_line_count() > 500:
            buffer.delete(buffer.get_start_iter(), buffer.get_iter_at_line(100))
        self.log_view.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 1)

    def start(self):
        self.body.pack_start(self.log_scroll, True, True, 0)
        self.button("Clear log", lambda: self.log_view.get_buffer().set_text(""))
        self.show_all()
        Gio.bus_get(Gio.BusType.SESSION, self.cancellable, self.bus_ready)

    def bus_ready(self, source, result):
        if self.cancellable.is_cancelled():
            return
        try:
            self.connection = Gio.bus_get_finish(result)
            self.subscription = self.connection.signal_subscribe(
                self.service, self.service, None, self.path, None,
                Gio.DBusSignalFlags.NONE, self.signal_received, None)
            self.log(f"Session bus connected: {self.service}")
            self.inspect()
        except GLib.Error as error:
            self.log(f"Session bus unavailable: {error}. Reopen the app to reconnect.")

    def call(self, method, parameters=None, callback=None, interface=None):
        if self.connection is None:
            self.log("Session bus is not connected.")
            return
        self.log(f"→ {method}")

        def finished(connection, result):
            if self.cancellable.is_cancelled():
                return
            try:
                reply = connection.call_finish(result)
                self.log(f"← {method}: {reply.print_(True)}")
                if callback:
                    callback(reply.unpack())
            except GLib.Error as error:
                self.log(f"{method} failed: {error}")

        self.connection.call(self.service, self.path, interface or self.service,
                             method, parameters, None, Gio.DBusCallFlags.NONE,
                             10000, self.cancellable, finished)

    def signal_received(self, connection, sender, path, interface, name, args, data):
        self.log(f"Signal {name}: {args.print_(True)}")

    def hints(self, entry):
        return GLib.Variant.parse(GLib.VariantType.new("a{sv}"),
                                  entry.get_text(), None, None)

    def cleanup(self, *_):
        self.cancellable.cancel()
        if self.connection and self.subscription is not None:
            self.connection.signal_unsubscribe(self.subscription)


def run(app_id, window_class):
    import sys
    app = Gtk.Application(application_id=app_id)

    def activate(application):
        window = application.props.active_window
        if window is None:
            window = window_class(application)
        window.present()

    app.connect("activate", activate)
    return app.run(sys.argv)
