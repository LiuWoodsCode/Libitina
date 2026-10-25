"""Network rows open connection dialogs; primary switches stay on the page."""

import shutil

from gi.repository import Gio, GLib, Gtk

import system_backend as backend
from connection_page import edit_connection
from service_page import (
    PreferencesPage, ServicePage, action_row, choice_row, combo, field, form,
    group, present_dialog, switch_row,
)


def connection_editor(page, profile_uuid=None):
    executable = shutil.which("nm-connection-editor")
    if executable is None:
        page.status.set_text("Install nm-connection-editor for VPN import, enterprise security, and advanced settings.")
        return
    try:
        Gio.Subprocess.new([executable, *(["--edit", profile_uuid] if profile_uuid else [])], Gio.SubprocessFlags.NONE)
    except GLib.Error as exc:
        page.status.set_text(exc.message)


def profile_rows(page, container, profiles):
    for uid, name, kind, device in profiles:
        active = device not in ("", "--")
        icon = "network-vpn-symbolic" if kind in ("vpn", "wireguard") else "network-cellular-symbolic" if kind in ("gsm", "cdma") else "network-wired-symbolic"
        action_row(container, name, f"Connected · {device}" if active else "Not connected", icon,
                   callback=lambda uid=uid, title=name, active=active: edit_connection(page, uid, title, active))


class WifiPage(ServicePage):
    def __init__(self):
        self.interface = ""
        self._scan = False
        super().__init__("Scan for networks")

    def reload(self):
        self._scan = True
        super().reload()

    def load(self):
        scan, self._scan = self._scan, False
        return backend.wifi_snapshot(self.interface, scan)

    def _saved_for(self, ssid, advertised):
        keys = ["wpa-eap", "wpa-eap-suite-b-192"] if "802.1X" in advertised else (
            ["sae", "wpa-psk"] if "WPA3" in advertised else ["wpa-psk"] if "WPA" in advertised else
            ["owe"] if "OWE" in advertised else ["none"] if "WEP" in advertised else ["open"])
        return next((self._saved_wifi[(ssid, key)] for key in keys if (ssid, key) in self._saved_wifi), None)

    def render(self, data):
        self.interface = data["interface"]
        self._saved_wifi, self._profiles = data["saved_wifi"], data["profiles"]
        enabled = data["state"][4] == "enabled"
        controls = group(self.body)
        switch_row(controls, "Wi-Fi", enabled,
                   lambda active: self.perform(lambda: backend.nm("radio", "wifi", "on" if active else "off")),
                   subtitle="Blocked by hardware or airplane mode" if data["state"][3] != "enabled" else "")
        if len(data["adapters"]) > 1:
            choice_row(controls, "Adapter", [(row[0], row[0]) for row in data["adapters"]], self.interface, self._adapter_changed)
        nearby = group(self.body, "Networks")
        access_points = {}
        for item in data["access_points"]:
            key = (item[1], item[4])
            old = access_points.get(key)
            if old is None or (item[0] == "*", int(item[3])) > (old[0] == "*", int(old[3])):
                access_points[key] = item
        visible = 0
        for used, ssid, _bssid, signal, security, _path in sorted(
            access_points.values(), key=lambda item: (item[0] != "*", -int(item[3]), item[1])
        ):
            if not ssid:
                continue
            visible += 1
            connected = used == "*"
            saved = self._saved_for(ssid, security)
            if connected and not saved:
                saved = next((item[0] for item in self._profiles if item[2] in ("802-11-wireless", "wifi") and item[3] == self.interface), None)
            quality = "excellent" if int(signal) >= 80 else "good" if int(signal) >= 55 else "ok" if int(signal) >= 30 else "weak"
            subtitle = "Connected" if connected else "Saved" if saved else security or "Open network"
            if security and (connected or saved):
                subtitle += " · " + security
            callback = (lambda uid=saved, name=ssid, active=connected: edit_connection(self, uid, name, active)) if saved else (
                lambda name=ssid, security=security: self._join(name, security))
            action_row(nearby, ssid, subtitle, f"network-wireless-signal-{quality}-symbolic", callback)
        if not visible:
            title = "No Wi-Fi adapter" if not data["adapters"] else "Wi-Fi is off" if not enabled else "No networks found"
            action_row(nearby, title, icon="network-wireless-offline-symbolic")
        options = group(self.body)
        action_row(options, "Saved networks", icon="document-open-recent-symbolic", callback=self._saved_networks)
        hidden = action_row(options, "Connect to hidden network…", icon="list-add-symbolic", callback=lambda: self._join(hidden=True))
        hidden.set_sensitive(enabled and bool(self.interface))

    def _adapter_changed(self, value):
        self.interface = value or ""
        self.reload()

    def _saved_networks(self):
        page = PreferencesPage()
        saved = [item for item in self._profiles if item[2] in ("802-11-wireless", "wifi")]
        rows = group(page.body)
        dialog = present_dialog(self, "Saved networks", page)
        for uid, name, _kind, device in saved:
            def edit(uid=uid, name=name, active=device not in ("", "--")):
                dialog.destroy()
                edit_connection(self, uid, name, active)
            action_row(rows, name, "Connected" if device not in ("", "--") else "Saved", "network-wireless-symbolic", edit)
        if not saved:
            action_row(rows, "No saved networks", icon="network-wireless-symbolic")
        page.show_all()
    def _join(self, ssid="", advertised="", hidden=False):
        security = "open"
        if "802.1X" in advertised:
            security = "wpa-eap"
        elif "WEP" in advertised:
            security = "none"
        elif "WPA3" in advertised:
            security = "sae"
        elif "WPA" in advertised:
            security = "wpa-psk"
        elif "OWE" in advertised:
            security = "owe"
        saved = self._saved_wifi.get((ssid, security))
        if not saved and "WPA2" in advertised and "802.1X" not in advertised:
            saved = self._saved_wifi.get((ssid, "wpa-psk"))
        if saved and not hidden:
            interface = self.interface
            self.perform(lambda: backend.activate_profile(saved, interface), "Connected using the saved Wi-Fi connection.")
            return
        if security in ("wpa-eap", "none"):
            connection_editor(self)
            return
        if hidden:
            name = Gtk.Entry()
            security_widget = combo([
                ("open", "Open network"), ("wpa-psk", "WPA / WPA2 Personal"),
                ("sae", "WPA3 Personal"), ("owe", "Enhanced Open (OWE)"),
            ], security)
            password = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD)
            password.set_sensitive(False)
            security_widget.connect("changed", lambda widget: password.set_sensitive(widget.get_active_id() in ("wpa-psk", "sae")))
            values = form(self, "Hidden network", [("Network name", name), ("Security", security_widget), ("Password", password)])
        elif security in ("open", "owe"):
            values = [ssid, security, ""]
        else:
            password = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD)
            entered = form(self, ssid, [("Wi-Fi password", password)])
            values = [ssid, security, entered[0]] if entered is not None else None
        if values is None:
            return
        interface = self.interface

        def connect():
            uid = backend.create_wifi(*values, hidden=hidden)
            try:
                backend.activate_profile(uid, interface)
            except backend.ServiceError as exc:
                raise backend.ServiceError(f"{exc}\nThe profile was saved. Edit it or retry under Saved connections.") from exc

        self.perform(connect, "Connected.")


class NetworkPage(ServicePage):
    def load(self):
        data = backend.network_snapshot()
        data["details"] = backend.nm("--fields", "GENERAL.DEVICE,GENERAL.STATE,GENERAL.HWADDR,IP4,IP6", "device", "show")
        return data

    def render(self, data):
        controls = group(self.body)
        switch_row(controls, "Networking", data["state"][2] == "enabled",
                   lambda active: self.perform(lambda: backend.nm("networking", "on" if active else "off")),
                   subtitle="Internet connected" if data["state"][1] == "full" else data["state"][0].capitalize())
        wired = [item for item in data["devices"] if item[1] == "ethernet"]
        if wired:
            rows = group(self.body, "Wired")
            for interface, _kind, state, connection in wired:
                profile = next((item for item in data["profiles"] if item[3] == interface), None)
                if profile is None:
                    profile = next((item for item in data["profiles"] if item[2] in ("ethernet", "802-3-ethernet")), None)
                active = state.startswith("connected")
                control = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
                control.set_sensitive(state not in ("unavailable", "unmanaged"))
                control.connect("notify::active", lambda widget, *_args, name=interface: self.perform(
                    lambda active=widget.get_active(): backend.nm("device", "connect" if active else "disconnect", name)))
                action_row(rows, connection if active else interface, state.capitalize(), "network-wired-symbolic",
                           callback=(lambda uid=profile[0], title=profile[1], active=active: edit_connection(self, uid, title, active))
                           if profile else lambda: connection_editor(self), suffix=control)
        vpn = group(self.body, "VPN")
        profiles = [item for item in data["profiles"] if item[2] in ("vpn", "wireguard")]
        profile_rows(self, vpn, profiles)
        action_row(vpn, "Add VPN…", icon="list-add-symbolic", callback=lambda: connection_editor(self))
        other = [item for item in data["profiles"] if item[2] not in
                 ("vpn", "wireguard", "ethernet", "802-3-ethernet", "802-11-wireless", "wifi", "gsm", "cdma", "loopback")]
        if other:
            profile_rows(self, group(self.body, "Other connections"), other)
        options = group(self.body)
        action_row(options, "Connection details", icon="dialog-information-symbolic", callback=lambda: self._details(data["details"]))
        action_row(options, "Add connection…", icon="list-add-symbolic", callback=lambda: connection_editor(self))

    def _details(self, text):
        page = PreferencesPage()
        current = None
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            if key == "GENERAL.DEVICE":
                current = group(page.body, value)
            elif current is not None and value and value != "--":
                title = {"GENERAL.STATE": "Status", "GENERAL.HWADDR": "Hardware address"}.get(key)
                if title is None:
                    title = key.replace("IP4.", "IPv4 ").replace("IP6.", "IPv6 ").split("[")[0].replace("ADDRESS", "address").replace("GATEWAY", "gateway")
                action_row(current, title, value)
        present_dialog(self, "Connection details", page)


class CellularPage(ServicePage):
    def load(self):
        return backend.modem_snapshot()

    def render(self, data):
        controls = group(self.body)
        switch_row(controls, "Mobile data", data["state"][6] == "enabled",
                   lambda active: self.perform(lambda: backend.nm("radio", "wwan", "on" if active else "off")))
        modems = group(self.body, "SIM & modem")
        for path, modem in data["modems"]:
            generic, carrier = modem.get("generic", {}), modem.get("3gpp", {})
            name = " ".join(str(generic.get(key, "")) for key in ("manufacturer", "model")).strip() or path
            action_row(modems, carrier.get("operator-name") or name, generic.get("state", "Unknown").capitalize(),
                       "network-cellular-symbolic", lambda name=name, modem=modem: self._modem_details(name, modem))
        if not data["modems"]:
            action_row(modems, "No cellular modem", icon="network-cellular-offline-symbolic")
        saved = [item for item in data["profiles"] if item[2] in ("gsm", "cdma")]
        if saved:
            profile_rows(self, group(self.body, "Data connections"), saved)

    def _modem_details(self, name, modem):
        page = PreferencesPage()
        generic, carrier = modem.get("generic", {}), modem.get("3gpp", {})
        details = group(page.body)
        quality = generic.get("signal-quality", {})
        quality = quality.get("value", "Unknown") if isinstance(quality, dict) else quality
        for title, value in (("Carrier", carrier.get("operator-name", "Unknown")),
                             ("Status", generic.get("state", "Unknown")), ("Signal strength", f"{quality}%"),
                             ("SIM lock", generic.get("unlock-required", "Unknown"))):
            action_row(details, title, value)
        actions = group(page.body)
        dialog = present_dialog(self, name, page)
        sim = generic.get("sim", "")
        if sim and sim != "--" and generic.get("unlock-required") == "sim-pin":
            def unlock():
                dialog.destroy()
                self._unlock(sim)
            action_row(actions, "Unlock SIM…", f"Remaining attempts: {generic.get('unlock-retries', 'Unknown')}",
                       "changes-prevent-symbolic", unlock)
        def add_apn():
            dialog.destroy()
            self._add_apn(generic.get("device-identifier", ""))
        action_row(actions, "Add APN connection…", icon="list-add-symbolic", callback=add_apn)
        page.show_all()

    def _unlock(self, sim):
        values = form(self, "Unlock SIM", [("SIM PIN", Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PIN))])
        if values:
            self.perform(lambda: backend.unlock_sim(sim, values[0]), "SIM unlocked.")

    def _add_apn(self, device_id):
        values = form(self, "Mobile data connection", [
            ("Connection name", Gtk.Entry(text="Mobile data")), ("APN", Gtk.Entry()),
            ("Username (optional)", Gtk.Entry()), ("Password (optional)", Gtk.Entry(visibility=False)),
            ("Roaming", Gtk.CheckButton(label="Allow data roaming")),
        ])
        if values:
            self.perform(lambda: backend.create_cellular(*values, device_id), "APN saved. Choose a data connection to connect.")
