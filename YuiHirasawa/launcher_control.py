"""Session-bus command for opening the running shell's existing launcher."""

from gi.repository import Gio, GLib

NAME = "org.libitina.Yui"
PATH = "/org/libitina/Yui"
INTERFACE = "org.libitina.Yui.Launcher"


class LauncherControl:
    def __init__(self, launcher):
        self._launcher = launcher
        self._connection = None
        self._interface = Gio.DBusNodeInfo.new_for_xml(
            f'<node><interface name="{INTERFACE}"><method name="Open"/></interface></node>'
        ).interfaces[0]
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, NAME,
                                       Gio.BusNameOwnerFlags.NONE, self._acquired, None, None)

    def _acquired(self, connection, _name):
        self._connection = connection
        self._registration = connection.register_object(
            PATH, self._interface, self._call, None, None)

    def _call(self, _connection, _sender, _path, _interface, _method, _parameters, invocation):
        opener = getattr(self._launcher, "open", None)
        if opener is not None:
            opener()
        else:
            self._launcher.show_all()
            self._launcher.present()
        invocation.return_value(None)

    def close(self):
        if self._connection:
            self._connection.unregister_object(self._registration)
        Gio.bus_unown_name(self._owner)


def open_launcher():
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        connection.call_sync(NAME, PATH, INTERFACE, "Open", None, None,
                             Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
    except GLib.Error as exc:
        import sys
        print(f"Could not open Yui launcher: {exc.message}", file=sys.stderr)
        return 1
    return 0
