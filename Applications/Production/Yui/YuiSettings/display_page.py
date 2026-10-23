"""Display controls backed by the Yui session's monitor configuration."""

from pathlib import Path
import json
import sys
import threading
import time

from gi.repository import GLib, Gtk

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "YuiHirasawa"))
import displays


class DisplayPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_border_width(24)
        self._outputs = []
        self._profiles = []
        self._editors = []
        self._alive = True
        self._revision = None
        self._saved_at = 0
        self.connect("destroy", self._destroyed)
        description = Gtk.Label(
            label="Configure connected monitors. Save and Apply also restores these settings when Yui starts.",
            xalign=0, wrap=True)
        description.set_max_width_chars(48)
        self.pack_start(description, False, False, 0)
        self._refresh = Gtk.Button(label="Refresh displays")
        self._refresh.connect("clicked", self._reload)
        self.pack_start(self._refresh, False, False, 0)
        self._current = Gtk.Button(label="Use current display settings")
        self._current.set_tooltip_text("Read the active layout instead of the saved configuration.")
        self._current.connect("clicked", lambda *_: self._reload(use_saved=False))
        self.pack_start(self._current, False, False, 0)
        self._status = Gtk.Label(xalign=0, wrap=True)
        self._status.set_max_width_chars(48)
        self._status.set_selectable(True)
        self.pack_start(self._status, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        scroll.add(self._list)
        self.pack_start(scroll, True, True, 0)
        self._save = Gtk.Button(label="Save and Apply")
        self._save.connect("clicked", self._save_clicked)
        self._save.set_sensitive(False)
        self.pack_start(self._save, False, False, 0)
        self._timer = GLib.timeout_add_seconds(2, self._check_status)
        self._reload()

    def _destroyed(self, *_args):
        self._alive = False
        GLib.source_remove(self._timer)

    def _reload(self, *_args, use_saved=True):
        self._refresh.set_sensitive(False)
        self._current.set_sensitive(False)
        self._save.set_sensitive(False)
        self._revision = None
        self._status.set_text("Reading connected displays…")

        def read():
            try:
                outputs = displays.query_outputs()
                profiles = displays.snapshot(outputs)
                if use_saved and displays.CONFIG_PATH.exists():
                    saved = {profile["name"]: profile for profile in displays.load_profiles()}
                    profiles = [saved.get(profile["name"], profile) for profile in profiles]
                    # Retain disconnected monitors when saving the current layout.
                    names = {profile["name"] for profile in profiles}
                    profiles += [profile for name, profile in saved.items() if name not in names]
                GLib.idle_add(self._loaded, outputs, profiles, None)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                GLib.idle_add(self._loaded, [], [], str(exc))

        threading.Thread(target=read, daemon=True).start()

    def _loaded(self, outputs, profiles, error):
        if not self._alive:
            return False
        self._refresh.set_sensitive(True)
        self._current.set_sensitive(True)
        for child in self._list.get_children():
            child.destroy()
        self._outputs, self._profiles, self._editors = outputs, profiles, []
        if error:
            self._status.set_text(error)
            return False
        self._status.set_text("" if outputs else "No displays found.")
        by_name = {profile["name"]: profile for profile in profiles}
        for output in outputs:
            self._add_output(output, by_name[output["name"]])
        self._list.show_all()
        self._save.set_sensitive(bool(outputs))
        return False

    def _add_output(self, output, profile):
        frame = Gtk.Frame(label=output["name"])
        grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=12)
        frame.add(grid)
        row = 0

        def field(title, widget):
            nonlocal row
            label = Gtk.Label(label=title, xalign=0)
            grid.attach(label, 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            row += 1
            return widget

        description = Gtk.Label(label=output.get("description") or output["name"],
                                xalign=0, wrap=True)
        description.set_max_width_chars(32)
        grid.attach(description, 0, row, 2, 1)
        row += 1
        enabled = field("Enabled", Gtk.Switch(active=profile["enabled"]))
        enabled.set_halign(Gtk.Align.START)
        mode = Gtk.ComboBoxText.new_with_entry()
        mode.get_child().set_width_chars(12)
        mode.append_text("")
        for item in output.get("modes", []):
            mode.append_text(displays.mode_name(item))
        mode.get_child().set_text(profile.get("mode", ""))
        mode.set_tooltip_text("Width x height, optionally @refreshHz. Leave empty to use the compositor's default.")
        field("Resolution", mode)
        custom = field("Custom mode", Gtk.CheckButton())
        custom.set_active(profile.get("custom_mode", False))
        custom.set_tooltip_text("Enable to use an unlisted resolution or refresh rate.")
        scale = field("Scale", Gtk.Entry(text=str(profile.get("scale", 1))))
        scale.set_width_chars(8)
        transform = Gtk.ComboBoxText()
        for value in displays.TRANSFORMS:
            transform.append(value, value)
        transform.set_active_id(profile.get("transform", "normal"))
        field("Transform", transform)
        x = field("Position X", Gtk.Entry(text=str(profile["position"]["x"])))
        y = field("Position Y", Gtk.Entry(text=str(profile["position"]["y"])))
        for entry in (x, y):
            entry.set_width_chars(8)
        adaptive = field("Adaptive sync", Gtk.CheckButton())
        adaptive.set_active(profile.get("adaptive_sync") is True)
        adaptive.set_sensitive(output.get("adaptive_sync") is not None)
        if output.get("adaptive_sync") is None:
            adaptive.set_tooltip_text("Not supported by this compositor.")
        self._editors.append((profile, enabled, mode, custom, scale, transform, x, y, adaptive))
        self._list.pack_start(frame, False, False, 0)

    def _save_clicked(self, *_args):
        try:
            edited = {}
            for profile, enabled, mode, custom, scale, transform, x, y, adaptive in self._editors:
                edited[profile["name"]] = {
                    "name": profile["name"], "enabled": enabled.get_active(),
                    "mode": mode.get_child().get_text().strip(),
                    "custom_mode": custom.get_active(),
                    "scale": float(scale.get_text()), "transform": transform.get_active_id(),
                    "position": {"x": int(x.get_text()), "y": int(y.get_text())},
                    "adaptive_sync": adaptive.get_active() if adaptive.get_sensitive() else None,
                }
            profiles = [edited.get(profile["name"], profile) for profile in self._profiles]
            displays.validate(profiles)
            displays.build_arguments(profiles, self._outputs)
            displays.save_profiles(profiles)
            self._profiles = profiles
            self._revision = displays.CONFIG_PATH.stat().st_mtime_ns
            self._saved_at = time.monotonic()
            self._status.set_text("Saved. Waiting for the Yui launcher to apply the settings…")
        except (OSError, ValueError) as exc:
            self._status.set_text(str(exc))

    def _check_status(self):
        if self._revision is not None:
            try:
                status = json.loads(displays.STATUS_PATH.read_text(encoding="utf-8"))
                if isinstance(status, dict) and status.get("revision") == self._revision:
                    self._status.set_text(status["message"])
                    return True
            except (OSError, ValueError, KeyError):
                pass
            if time.monotonic() - self._saved_at > 15:
                self._status.set_text("Saved, but the launcher has not responded. Start or restart the Yui session to apply these settings.")
        return True
