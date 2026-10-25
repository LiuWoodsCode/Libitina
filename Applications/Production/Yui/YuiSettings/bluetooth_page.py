"""BlueZ devices and a session-scoped pairing agent."""

import time

from gi.repository import Gio, GLib, Gtk

import system_backend as backend
from service_page import (ServicePage, action_row, choice_row, confirm, group, present_dialog, switch_row)


AGENT_PATH = "/org/libitina/Yui/Settings/Agent"
AGENT_XML = """<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="Cancel"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>"""


def bluez(path, interface, method, signature=None, values=(), timeout=25000):
    return backend.dbus_call("org.bluez", path, "org.bluez." + interface,
                             method, signature, values, timeout)


class BluetoothPage(ServicePage):
    def __init__(self):
        self.bus = None
        self.adapter = ""
        self._registration = None
        self._agent_registered = False
        self._scanning = None
        self._scan_timer = None
        self._dialog = None
        self._pending = None
        self._pairing = None
        self._names = {}
        self._bluez_owner = None
        super().__init__()
        self.connect("unmap", lambda *_: self._stop_scan())
        Gio.bus_get(Gio.BusType.SYSTEM, None, self._bus_ready)

    def _bus_ready(self, _source, result):
        if not self._alive:
            return
        try:
            self.bus = Gio.bus_get_finish(result)
            info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
            self._registration = self.bus.register_object(AGENT_PATH, info, self._agent_call, None, None)
        except GLib.Error as exc:
            self.status.set_text(exc.message)
            return
        if self.get_mapped():
            self.reload()

    def load(self):
        if self.bus is None:
            raise backend.ServiceError("Waiting for the system Bluetooth service. Refresh to try again.")
        self._bluez_owner = backend.dbus_call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                              "org.freedesktop.DBus", "GetNameOwner", "(s)", ("org.bluez",))[0]
        return backend.dbus_call(
            "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")[0]

    def render(self, objects):
        adapters = [(path, interfaces["org.bluez.Adapter1"]) for path, interfaces in objects.items()
                    if "org.bluez.Adapter1" in interfaces]
        if not adapters:
            action_row(group(self.body), "No Bluetooth adapter", icon="bluetooth-disabled-symbolic")
            return
        if self.adapter not in dict(adapters):
            self.adapter = adapters[0][0]
        adapter_props = dict(adapters)[self.adapter]
        controls = group(self.body)
        switch_row(controls, "Bluetooth", adapter_props.get("Powered", False),
                   lambda active: self.perform(lambda: self._set(self.adapter, "Adapter1", "Powered", active)))
        if len(adapters) > 1:
            choice_row(controls, "Adapter", [(path, props.get("Alias", path)) for path, props in adapters],
                       self.adapter, self._adapter_changed)
        self._names = {}
        devices = [(path, interfaces["org.bluez.Device1"]) for path, interfaces in objects.items()
                   if "org.bluez.Device1" in interfaces and interfaces["org.bluez.Device1"].get("Adapter") == self.adapter]
        for paired, title in ((True, "Paired devices"), (False, "Nearby devices")):
            matching = [(path, props) for path, props in devices if bool(props.get("Paired")) == paired]
            if not matching and paired:
                continue
            rows = group(self.body, title)
            for path, props in sorted(matching, key=lambda item: item[1].get("Alias", "")):
                name = props.get("Alias", props.get("Address", path))
                self._names[path] = name
                connected = props.get("Connected", False)
                action_row(rows, name, "Connected" if connected else "Not connected" if paired else "Ready to pair",
                           props.get("Icon") or "bluetooth-symbolic",
                           (lambda path=path, name=name: self._device_details(path, name)) if paired else
                           (lambda path=path: self._pair(path)))
            if not matching:
                action_row(rows, "No nearby devices" if adapter_props.get("Powered") else "Bluetooth is off",
                           icon="bluetooth-symbolic")
        options = group(self.body)
        scan = action_row(options, "Stop scanning" if self._scanning else "Scan for devices",
                          icon="process-stop-symbolic" if self._scanning else "view-refresh-symbolic",
                          callback=self._stop_and_reload if self._scanning else self._scan)
        scan.set_sensitive(adapter_props.get("Powered", False))

    def _device_details(self, path, name):
        page = BluetoothDevicePage(self, path, name)
        dialog = present_dialog(self, name, page)
        dialog.connect("destroy", lambda *_: self.reload() if self._alive else None)

    def _set(self, path, interface, prop, value):
        backend.dbus_call("org.bluez", path, "org.freedesktop.DBus.Properties", "Set", "(ssv)",
                          ("org.bluez." + interface, prop, GLib.Variant("b", value)))

    def _adapter_changed(self, widget):
        self._stop_scan()
        self.adapter = widget
        self.reload()

    def _scan(self):
        adapter = self.adapter
        self._scanning = adapter
        self._scan_until = time.monotonic() + 20
        self._scan_timer = GLib.timeout_add_seconds(3, self._poll_scan)

        def start():
            try:
                bluez(adapter, "Adapter1", "StartDiscovery")
            except backend.ServiceError:
                GLib.idle_add(self._stop_scan)
                raise
            # Leaving the page may race with the asynchronous discovery start.
            if self._scanning != adapter or not self._alive:
                bluez(adapter, "Adapter1", "StopDiscovery")

        self.perform(start, "Scanning for nearby devices…")

    def _poll_scan(self):
        if not self._alive or not self._scanning:
            self._scan_timer = None
            return False
        if time.monotonic() >= self._scan_until:
            self._scan_timer = None
            self._stop_and_reload()
            return False
        self.reload()
        return True

    def _stop_scan(self):
        if self._scan_timer:
            GLib.source_remove(self._scan_timer)
            self._scan_timer = None
        if self.bus and self._scanning:
            self._call_async(self._scanning, "Adapter1", "StopDiscovery")
        self._scanning = None

    def _stop_and_reload(self):
        self._stop_scan()
        self.reload()

    def _call_async(self, path, interface, method, signature=None, values=()):
        def done(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error:
                pass  # Best-effort session cleanup, including a stopped daemon.
        self.bus.call("org.bluez", path, "org.bluez." + interface, method,
                      GLib.Variant(signature, values) if signature else None, None,
                      Gio.DBusCallFlags.NONE, 5000, None, done)

    def _pair(self, device):
        self._stop_scan()
        self._pairing = device

        def pair():
            # Registration is scoped to this D-Bus connection; no default-agent
            # takeover is needed for pairing requests initiated by this app.
            try:
                bluez("/org/bluez", "AgentManager1", "RegisterAgent", "(os)", (AGENT_PATH, "KeyboardDisplay"))
                self._agent_registered = True
                bluez(device, "Device1", "Pair", timeout=120000)
                bluez(device, "Device1", "Connect")
            finally:
                if self._agent_registered:
                    try:
                        bluez("/org/bluez", "AgentManager1", "UnregisterAgent", "(o)", (AGENT_PATH,))
                    except backend.ServiceError:
                        pass
                    self._agent_registered = False
                GLib.idle_add(self._pair_finished)

        self.perform(pair, "Accessory paired and connected.")

    def _pair_finished(self):
        self._pairing = None
        self._close_prompt()
        return False

    def _close_prompt(self):
        if self._pending:
            self._pending.return_dbus_error("org.bluez.Error.Canceled", "Pairing was cancelled.")
            self._pending = None
        if self._dialog:
            self._dialog.destroy()
            self._dialog = None

    def _agent_call(self, _bus, _sender, _path, _interface, method, params, invocation):
        if _sender != self._bluez_owner:
            invocation.return_dbus_error("org.bluez.Error.Rejected", "Only BlueZ may use the pairing agent.")
            return
        args = params.unpack()
        if method in ("Release", "Cancel"):
            if method == "Release":
                self._agent_registered = False
            self._close_prompt()
            invocation.return_value(None)
            return
        # Only answer requests for the accessory the user chose to pair.
        if not args or args[0] != self._pairing or not self._alive:
            invocation.return_dbus_error("org.bluez.Error.Rejected", "No pairing requested for this device.")
            return
        self._close_prompt()
        name = self._names.get(args[0], args[0])
        text = f"Pair with {name}?"
        entry = None
        display = method in ("DisplayPasskey", "DisplayPinCode")
        if method == "RequestConfirmation":
            text = f"Does {name} show this code?\n{args[1]:06d}"
        elif display:
            code = f"{args[1]:06d}" if method == "DisplayPasskey" else args[1]
            text = f"Enter {code} on {name}, then press Enter on that accessory."
        elif method in ("RequestPinCode", "RequestPasskey"):
            text = f"Enter the pairing code for {name}."
            entry = Gtk.Entry()
        elif method == "AuthorizeService":
            text = f"Allow {name} to use this Bluetooth service?\n{args[1]}"
        dialog = Gtk.MessageDialog(transient_for=self.get_toplevel(), modal=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.CANCEL if display else Gtk.ButtonsType.OK_CANCEL,
                                   text=text)
        if entry:
            dialog.get_content_area().pack_start(entry, False, False, 8)
        self._dialog = dialog
        if display:
            invocation.return_value(None)
        else:
            self._pending = invocation

        def response(_dialog, response_id):
            pending, self._pending = self._pending, None
            if pending:
                value = entry.get_text() if entry else ""
                if response_id != Gtk.ResponseType.OK:
                    pending.return_dbus_error("org.bluez.Error.Rejected", "Pairing rejected.")
                elif method == "RequestPasskey" and not (value.isascii() and value.isdigit() and len(value) <= 6):
                    pending.return_dbus_error("org.bluez.Error.Rejected", "Passkey must contain 1–6 digits.")
                elif method == "RequestPinCode" and not 1 <= len(value) <= 16:
                    pending.return_dbus_error("org.bluez.Error.Rejected", "PIN must contain 1–16 characters.")
                else:
                    pending.return_value(GLib.Variant("(u)", (int(value),)) if method == "RequestPasskey" else
                                         GLib.Variant("(s)", (value,)) if method == "RequestPinCode" else None)
            elif display and self._pairing:
                self._call_async(self._pairing, "Device1", "CancelPairing")
            self._close_prompt()

        dialog.connect("response", response)
        dialog.show_all()

    def _destroyed(self, *_):
        super()._destroyed()
        self._stop_scan()
        self._close_prompt()
        if self.bus:
            if self._pairing:
                self._call_async(self._pairing, "Device1", "CancelPairing")
            if self._agent_registered:
                self._call_async("/org/bluez", "AgentManager1", "UnregisterAgent", "(o)", (AGENT_PATH,))
            if self._registration:
                self.bus.unregister_object(self._registration)


class BluetoothDevicePage(ServicePage):
    def __init__(self, owner, path, name):
        self.owner, self.path, self.name = owner, path, name
        super().__init__()

    def load(self):
        return backend.dbus_call("org.bluez", self.path, "org.freedesktop.DBus.Properties",
                                 "GetAll", "(s)", ("org.bluez.Device1",))[0]

    def render(self, props):
        controls = group(self.body)
        switch_row(controls, "Connected", props.get("Connected", False),
                   lambda active: self.perform(lambda: bluez(self.path, "Device1", "Connect" if active else "Disconnect")))
        switch_row(controls, "Trusted accessory", props.get("Trusted", False),
                   lambda active: self.perform(lambda: self.owner._set(self.path, "Device1", "Trusted", active)))
        action_row(group(self.body, "Device details"), "Bluetooth address", props.get("Address", "Unknown"))
        action_row(group(self.body), "Forget device…", icon="edit-delete-symbolic", callback=self._forget)

    def _forget(self):
        if confirm(self, f"Forget {self.name}?", "You will need to pair this accessory again."):
            adapter, path = self.owner.adapter, self.path
            self.owner.perform(lambda: bluez(adapter, "Adapter1", "RemoveDevice", "(o)", (path,)))
            self.get_toplevel().destroy()
