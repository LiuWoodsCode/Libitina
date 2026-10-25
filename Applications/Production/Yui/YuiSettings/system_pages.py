"""Native preference groups for audio, power, the clock, and text input."""

from datetime import datetime
import threading

from gi.repository import Gio, GLib, Gtk

import system_backend as backend
from service_page import (
    PreferencesPage, ServicePage, action_row, choice_row, confirm, field, form,
    group, present_dialog, switch_row,
)


class VolumeSlider(Gtk.Scale):
    """Coalesce slider edits without rebuilding the page or blocking a drag."""

    def __init__(self, page, node):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL,
                         adjustment=Gtk.Adjustment(value=min(node["volume"], 100), lower=0, upper=100,
                                                   step_increment=1, page_increment=5))
        self.set_digits(0)
        self.set_value_pos(Gtk.PositionType.RIGHT)
        self._page, self._node = page, node["id"]
        self._latest = int(self.get_value())
        self._timer, self._writing, self._alive, self._pending = None, False, True, False
        self.connect("value-changed", self._changed)
        self.connect("destroy", self._destroyed)

    def _changed(self, *_):
        self._latest = int(self.get_value())
        self._pending = True
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(180, self._commit)

    def _commit(self):
        self._timer = None
        if self._writing or not self._pending:
            return False
        value = self._latest
        self._writing, self._pending = True, False

        def write():
            error = ""
            try:
                backend.run("wpctl", "set-volume", self._node, f"{value}%", "--limit", "1.0")
            except backend.ServiceError as exc:
                error = str(exc)
            GLib.idle_add(done, error)

        def done(error):
            self._writing = False
            if error and self._page._alive:
                self._page.status.set_text(error)
            if self._pending:
                self._commit()
            return False

        threading.Thread(target=write, daemon=True).start()
        return False

    def _destroyed(self, *_):
        # Flush an edit if the user leaves immediately after moving the slider.
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None
        if self._pending and not self._writing:
            self._commit()
        self._alive = False


class AudioPage(ServicePage):
    def load(self):
        return backend.audio_snapshot()

    def render(self, nodes):
        for kind in ("Output", "Input"):
            devices = group(self.body, kind)
            matching = [node for node in nodes if node["kind"] == kind]
            if not matching:
                action_row(devices, f"No {kind.lower()} devices", icon="audio-speakers-symbolic" if kind == "Output" else "audio-input-microphone-symbolic")
                continue
            selected = next((node for node in matching if node.get("default")), matching[0])
            choice_row(devices, "Device", [(node["id"], node["name"]) for node in matching], selected["id"],
                       lambda value: self.perform(lambda: backend.run("wpctl", "set-default", value)))
            field(devices, "Volume", VolumeSlider(self, selected))
            switch_row(devices, "Mute microphone" if kind == "Input" else "Mute output", selected["muted"],
                       lambda active, device=selected["id"]: self.perform(
                           lambda: backend.run("wpctl", "set-mute", device, "1" if active else "0")))


BATTERY_STATES = {0: "Unknown", 1: "Charging", 2: "Discharging", 3: "Empty", 4: "Fully charged",
                  5: "Pending charge", 6: "Pending discharge"}


class PowerPage(ServicePage):
    def load(self):
        return backend.power_snapshot()

    def render(self, data):
        batteries = group(self.body, "Battery")
        for battery in data["batteries"]:
            percent = battery.get("Percentage", 0)
            level = Gtk.LevelBar(value=percent / 100)
            level.set_size_request(84, -1)
            action_row(batteries, battery.get("Model") or "Battery",
                       f"{percent:.0f}% · {BATTERY_STATES.get(battery.get('State'), 'Unknown')}",
                       "battery-symbolic", lambda battery=battery: self._details(battery), level)
        if not data["batteries"]:
            action_row(batteries, "Battery information unavailable" if data["battery_error"] else "No battery",
                       data["battery_error"], "battery-missing-symbolic")
        modes = group(self.body, "Power")
        if data["profiles"]:
            choice_row(modes, "Power mode", [(name, name.replace("-", " ").capitalize()) for name in data["profiles"]],
                       data["active"], lambda value: self.perform(lambda: backend.set_power_profile(value)))
        else:
            action_row(modes, "Power modes unavailable", data["profile_error"])
        action_row(modes, "Suspend…", icon="system-suspend-symbolic", callback=self._suspend)

    def _details(self, battery):
        page = PreferencesPage()
        rows = group(page.body)
        action_row(rows, "Battery health", f"{battery.get('Capacity', 0):.0f}% of original capacity")
        seconds = battery.get("TimeToEmpty") or battery.get("TimeToFull")
        if seconds:
            action_row(rows, "Time until full" if battery.get("TimeToFull") else "Time remaining",
                       f"{seconds // 3600}h {(seconds % 3600) // 60}m")
        for key, title in (("Vendor", "Manufacturer"), ("Model", "Model"), ("Serial", "Serial number")):
            if battery.get(key):
                action_row(rows, title, battery[key])
        present_dialog(self, "Battery details", page, height=380)

    def _suspend(self):
        if confirm(self, "Suspend this device?", "The system will sleep until you wake it."):
            self.perform(lambda: backend.dbus_call(
                "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                "Suspend", "(b)", (True,)))


class DateTimePage(ServicePage):
    def __init__(self):
        self._zones = None
        super().__init__()

    def load(self):
        data = backend.clock_snapshot()
        if self._zones is None:
            self._zones = backend.run("timedatectl", "list-timezones").splitlines()
        return data

    def render(self, data):
        clock = group(self.body)
        automatic = switch_row(clock, "Automatic date & time", data.get("NTP") == "yes",
                               lambda active: self.perform(lambda: backend.set_clock("SetNTP", "(bb)", active)),
                               "Synchronized" if data.get("NTPSynchronized") == "yes" else "")
        automatic.set_sensitive(data.get("CanNTP") == "yes")
        manual = action_row(clock, "Date & time", datetime.now().astimezone().strftime("%A, %d %B %Y · %H:%M"),
                            "preferences-system-time-symbolic", self._manual)
        manual.set_sensitive(data.get("NTP") != "yes")
        action_row(group(self.body), "Time zone", data.get("Timezone", "Unknown").replace("_", " "),
                   "find-location-symbolic", lambda: self._timezone(data.get("Timezone", "")))

    def _timezone(self, current):
        entry = Gtk.Entry(text=current)
        completion = Gtk.EntryCompletion()
        model = Gtk.ListStore(str)
        for name in self._zones:
            model.append([name])
        completion.set_model(model)
        completion.set_text_column(0)
        entry.set_completion(completion)
        values = form(self, "Time zone", [("Time zone", entry)])
        if values:
            name = values[0].strip()
            if name not in self._zones:
                self.status.set_text("Choose a valid time zone from the suggestions.")
                return
            self.perform(lambda: backend.set_clock("SetTimezone", "(sb)", name))

    def _manual(self):
        values = form(self, "Set date & time", [
            ("Local date and time", Gtk.Entry(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
        ])
        if not values:
            return
        try:
            microseconds = int(datetime.strptime(values[0], "%Y-%m-%d %H:%M:%S").timestamp() * 1_000_000)
        except (ValueError, OverflowError):
            self.status.set_text("Enter a valid date and time as YYYY-MM-DD HH:MM:SS.")
            return
        self.perform(lambda: backend.set_clock("SetTime", "(xbb)", microseconds, False))


class AccessibilityPage(PreferencesPage):
    def __init__(self):
        super().__init__()
        self._settings = []
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.gnome.desktop.interface", True) if source else None
        if schema is None:
            action_row(group(self.body), "Text preferences unavailable", "Install gsettings-desktop-schemas")
            return
        settings = Gio.Settings.new_full(schema, None, None)
        self._settings.append(settings)
        text = group(self.body, "Text")
        if schema.has_key("font-name"):
            row = action_row(text, "Interface font", callback=lambda: self._choose_font(settings))
            settings.bind("font-name", row, "subtitle", Gio.SettingsBindFlags.GET)
            row.set_sensitive(settings.is_writable("font-name"))
        if schema.has_key("text-scaling-factor"):
            scale = Gtk.SpinButton.new_with_range(0.75, 3.0, 0.05)
            scale.set_digits(2)
            settings.bind("text-scaling-factor", scale, "value", Gio.SettingsBindFlags.DEFAULT)
            field(text, "Text scaling", scale)
        interaction = group(self.body, "Pointer & interaction")
        for key, title in (("enable-animations", "Interface animations"), ("cursor-blink", "Blink text cursor")):
            if schema.has_key(key):
                widget = switch_row(interaction, title, False)
                settings.bind(key, widget, "active", Gio.SettingsBindFlags.DEFAULT)
        if schema.has_key("cursor-size"):
            size = Gtk.SpinButton.new_with_range(16, 96, 4)
            settings.bind("cursor-size", size, "value", Gio.SettingsBindFlags.DEFAULT)
            field(interaction, "Pointer size", size)

    def _choose_font(self, settings):
        dialog = Gtk.FontChooserDialog(title="Interface font", transient_for=self.get_toplevel(), modal=True)
        dialog.set_font(settings.get_string("font-name"))
        if dialog.run() == Gtk.ResponseType.OK:
            settings.set_string("font-name", dialog.get_font())
        dialog.destroy()
