"""Connection options presented on demand, instead of repeated on every row."""

from gi.repository import GObject, Gtk

import system_backend as backend
from service_page import ServicePage, action_row, choice_row, confirm, field, group, present_dialog


class ConnectionPage(ServicePage):
    def __init__(self, profile_uuid, owner=None, title="Connection", active=False):
        self.profile_uuid, self.owner, self.title, self.active = profile_uuid, owner, title, active
        super().__init__()
        self.save_button = Gtk.Button(label="Save", valign=Gtk.Align.CENTER)
        self.save_button.connect("clicked", self._save)
        self.save_button.set_tooltip_text("Save changes; reconnect to apply them")
        self.body.bind_property("sensitive", self.save_button, "sensitive", GObject.BindingFlags.SYNC_CREATE)

    def load(self):
        return backend.profile_settings(self.profile_uuid)

    def render(self, settings):
        self._families = []
        connection = settings.get("connection", {})
        general = group(self.body, "Connection")
        self._automatic = Gtk.Switch(active=connection.get("autoconnect", True))
        field(general, "Connect automatically", self._automatic)
        self._metered = choice_row(general, "Data usage", [
            ("unknown", "Automatic"), ("yes", "Metered"), ("no", "Unmetered"),
        ], {1: "yes", 2: "no"}.get(connection.get("metered"), "unknown"))
        for version in (4, 6):
            current = settings.get(f"ipv{version}", {})
            box = group(self.body, f"IPv{version}")
            methods = [("auto", "Automatic"), ("manual", "Manual"), ("link-local", "Link-local only"), ("disabled", "Disabled")]
            method = current.get("method", "auto")
            if method not in dict(methods):
                methods.append((method, method.capitalize()))
            method_widget = choice_row(box, "Address configuration", methods, method)
            addresses = field(box, "Addresses", Gtk.Entry(text=", ".join(
                f"{item['address']}/{item['prefix']}" for item in current.get("address-data", []))))
            addresses.set_placeholder_text("192.168.1.10/24" if version == 4 else "2001:db8::10/64")
            addresses.set_tooltip_text("Separate multiple addresses with commas")
            gateway = field(box, "Gateway", Gtk.Entry(text=current.get("gateway", "")))
            dns = field(box, "DNS servers", Gtk.Entry(text=", ".join(backend.dns_servers(current, version))))
            dns.set_tooltip_text("Separate multiple DNS servers with commas")
            ignore = Gtk.Switch(active=current.get("ignore-auto-dns", False))
            field(box, "Override automatic DNS", ignore)

            def sensitivity(widget, *_args, addr=addresses, gw=gateway, servers=dns, only=ignore):
                selected = widget.get_active_id()
                addr.set_sensitive(selected == "manual")
                gw.set_sensitive(selected == "manual")
                servers.set_sensitive(selected in ("auto", "manual"))
                only.set_sensitive(selected in ("auto", "manual"))

            method_widget.connect("notify::selected-index", sensitivity)
            sensitivity(method_widget)
            self._families.append((method_widget, addresses, gateway, dns, ignore))
        if self.owner:
            actions = group(self.body)
            action_row(actions, "Disconnect" if self.active else "Connect", icon="network-offline-symbolic" if self.active else "network-transmit-receive-symbolic",
                       callback=lambda: self._owner_action(lambda: backend.nm("connection", "down", "uuid", self.profile_uuid)
                                                          if self.active else backend.activate_profile(self.profile_uuid)))
            action_row(actions, "Advanced settings", icon="preferences-system-symbolic", callback=self._advanced)
            action_row(actions, "Forget connection…", icon="edit-delete-symbolic", callback=self._forget)

    def _advanced(self):
        from network_pages import connection_editor
        connection_editor(self, self.profile_uuid)

    def _owner_action(self, operation):
        owner = self.owner
        # Start the action before destroying the dialog: its destroy handler also
        # refreshes the owner, and must not race the requested operation.
        owner.perform(operation)
        self.get_toplevel().destroy()

    def _forget(self):
        if confirm(self, f"Forget {self.title}?", "This removes its saved settings and credentials and disconnects it if active."):
            self._owner_action(lambda: backend.nm("connection", "delete", "uuid", self.profile_uuid))

    def _save(self, *_):
        families = [{"method": method.get_active_id(), "addresses": addresses.get_text(),
                     "gateway": gateway.get_text(), "dns": dns.get_text(), "ignore_auto_dns": ignore.get_active()}
                    for method, addresses, gateway, dns, ignore in self._families]
        auto, metered = self._automatic.get_active(), self._metered.get_active_id()
        self.perform(lambda: backend.save_network_preferences(self.profile_uuid, auto, metered, families),
                     "Saved. Reconnect to apply changes.")


def edit_connection(parent, profile_uuid, title, active=False):
    page = ConnectionPage(profile_uuid, parent, title, active)
    dialog = present_dialog(parent, title, page, width=500, height=580)
    dialog.get_header_bar().pack_end(page.save_button)
    page.save_button.show()
    dialog.connect("destroy", lambda *_: parent.reload() if parent._alive else None)
    return dialog
