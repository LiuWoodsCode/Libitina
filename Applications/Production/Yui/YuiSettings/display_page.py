"""Monitor rows with on-demand editors and an explicit Apply action."""

from pathlib import Path
import json
import sys
import threading
import time
import shutil

from gi.repository import GLib, Gtk
from service_page import (PreferencesPage, action_row, choice_row, confirm, field, group, label, make_refresh_button, size_dialog)

# Installed copies bundle the backend; source checkouts share it with the shell.
if not Path(__file__).with_name("displays.py").is_file():
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "YuiHirasawa"))
import displays
import system_backend


class BrightnessControls(Gtk.Box):
    """Immediate hardware brightness controls, independent of layout Apply."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._alive = True
        self._busy = False
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pack_start(self._rows, False, False, 0)
        self._status = label()
        self.pack_start(self._status, False, False, 0)
        self.connect("destroy", self._destroyed)
        self.connect("map", self.reload)

    def _destroyed(self, *_):
        self._alive = False

    def reload(self, *_args, operation=None):
        if self._busy or not self._alive:
            return
        self._busy = True
        self._rows.set_sensitive(False)

        def work():
            error = ""
            try:
                if operation:
                    operation()
            except (OSError, ValueError, system_backend.ServiceError) as exc:
                error = str(exc)
            devices = system_backend.backlights()
            GLib.idle_add(done, devices, error)

        def done(devices, error):
            if not self._alive:
                return False
            self._busy = False
            for child in self._rows.get_children():
                child.destroy()
            rows = group(self._rows, "Brightness")
            available = shutil.which("brightnessctl") is not None
            for device in devices:
                slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 100, 1)
                slider.set_digits(0)
                slider.set_value(device["percent"])
                slider.set_sensitive(available)
                slider.set_tooltip_text("Brightness (%)")
                field(rows, device["name"], slider)
                button = Gtk.Button(label="Set brightness")
                button.set_sensitive(available)
                button.connect("clicked", lambda _button, name=device["name"], scale=slider:
                               self.reload(operation=lambda: system_backend.set_brightness(name, scale.get_value())))
                action_row(rows, "Apply brightness", "Applies immediately", suffix=button)
            if not devices:
                action_row(rows, "Brightness control unavailable",
                           "No supported hardware backlight was found.")
            self._status.set_text(error or ("Install brightnessctl to change brightness." if devices and not available else ""))
            self._rows.set_sensitive(True)
            self.show_all()
            return False

        threading.Thread(target=work, daemon=True).start()


class DisplayPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._outputs, self._profiles = [], []
        self._alive, self._busy, self._dirty = True, False, False
        self._revision, self._saved_at = None, 0
        self.connect("destroy", self._destroyed)
        self.refresh_button = self._refresh = make_refresh_button("Refresh displays", self._reload)
        self._save = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER, sensitive=False)
        self._save.set_no_show_all(True)
        self._save.connect("clicked", self._save_clicked)
        self.header_actions = [self._save]
        page = PreferencesPage()
        self._brightness = BrightnessControls()
        page.body.pack_start(self._brightness, False, False, 0)
        self._status = label()
        self._status.set_selectable(True)
        self._status.set_no_show_all(True)
        self._status.connect("notify::label", lambda widget, *_: widget.set_visible(bool(widget.get_text())))
        page.body.pack_start(self._status, False, False, 0)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.body.pack_start(self._list, False, False, 0)
        self.pack_start(page, True, True, 0)
        self._timer = GLib.timeout_add_seconds(2, self._check_status)
        self.connect("map", self._reload)

    def _destroyed(self, *_args):
        self._alive = False
        GLib.source_remove(self._timer)

    def _reload(self, *_args, use_saved=True):
        self._brightness.reload()
        if self._busy or not self._alive:
            return
        if self._dirty and not confirm(self, "Discard display changes?", "The changes have not been applied yet."):
            return
        self._busy = True
        self._refresh.set_sensitive(False)
        self._list.set_sensitive(False)
        self._save.set_sensitive(False)
        self._save.hide()
        self._revision = None
        self._status.set_text("Reading connected displays…")

        def read():
            try:
                outputs = displays.query_outputs()
                profiles = displays.snapshot(outputs)
                if use_saved and displays.CONFIG_PATH.exists():
                    saved = {profile["name"]: profile for profile in displays.load_profiles()}
                    profiles = [saved.get(profile["name"], profile) for profile in profiles]
                    names = {profile["name"] for profile in profiles}
                    profiles += [profile for name, profile in saved.items() if name not in names]
                GLib.idle_add(self._loaded, outputs, profiles, None)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                GLib.idle_add(self._loaded, [], [], str(exc))

        threading.Thread(target=read, daemon=True).start()

    def _loaded(self, outputs, profiles, error):
        if not self._alive:
            return False
        self._busy, self._dirty = False, False
        self._refresh.set_sensitive(True)
        self._list.set_sensitive(not error)
        self._outputs, self._profiles = outputs, profiles
        self._status.set_text(error or "")
        self._render()
        return False

    def _render(self):
        for child in self._list.get_children():
            child.destroy()
        monitors = group(self._list, "Displays")
        by_name = {profile["name"]: profile for profile in self._profiles}
        for output in self._outputs:
            profile = by_name[output["name"]]
            summary = f"{profile.get('mode') or 'Automatic resolution'} · {profile.get('scale', 1):g}×" if profile["enabled"] else "Disabled"
            action_row(monitors, output.get("description") or output["name"], summary,
                       "video-display-symbolic", lambda output=output: self._edit_output(output))
        if not self._outputs:
            action_row(monitors, "No displays found", icon="video-display-symbolic")
        action_row(group(self._list), "Use current layout", icon="view-restore-symbolic",
                   callback=lambda: self._reload(use_saved=False))
        self._list.show_all()

    def _edit_output(self, output):
        profile = next(item for item in self._profiles if item["name"] == output["name"])
        dialog = Gtk.Dialog(title=output.get("description") or output["name"], transient_for=self.get_toplevel(),
                            modal=True, use_header_bar=1)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Done", Gtk.ResponseType.OK)
        size_dialog(dialog, self, 440, 560)
        page = PreferencesPage()
        general = group(page.body)
        enabled = field(general, "Enabled", Gtk.Switch(active=profile["enabled"]))
        available_modes = [displays.mode_name(item) for item in output.get("modes", [])]
        saved_mode = profile.get("mode", "")
        is_custom = profile.get("custom_mode", False) or bool(saved_mode and saved_mode not in available_modes)
        custom = field(general, "Custom resolution", Gtk.Switch(active=is_custom))
        presets = Gtk.ComboBoxText()
        presets.append("", "Automatic")
        for value in available_modes:
            presets.append(value, value)
        presets.set_active_id(saved_mode if saved_mode in available_modes else "")
        custom_mode = Gtk.Entry(text=saved_mode, placeholder_text="1920x1080@60Hz", width_chars=12)
        custom_mode.set_tooltip_text("Width x height, optionally @refreshHz, for example 1600x900@75Hz.")
        mode = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, homogeneous=False)
        mode.add_named(presets, "preset")
        mode.add_named(custom_mode, "custom")
        field(general, "Resolution", mode)

        def custom_changed(widget, *_):
            active = widget.get_active()
            mode.set_visible_child_name("custom" if active else "preset")
            if active and dialog.get_mapped():
                custom_mode.grab_focus()

        custom.connect("notify::active", custom_changed)
        scale = Gtk.SpinButton.new_with_range(0.25, max(8, profile.get("scale", 1)), 0.25)
        scale.set_digits(2)
        scale.set_value(profile.get("scale", 1))
        field(general, "Scale", scale)
        transform = choice_row(general, "Orientation", [(value, value.replace("flipped", "Flipped").replace("normal", "Normal"))
                                                        for value in displays.TRANSFORMS], profile.get("transform", "normal"))
        position = group(page.body, "Arrangement")
        coordinates = []
        for axis in ("x", "y"):
            entry = Gtk.Entry(text=str(profile["position"][axis]))
            field(position, "Position " + axis.upper(), entry)
            coordinates.append(entry)
        advanced = group(page.body, "Advanced")
        adaptive = field(advanced, "Adaptive sync", Gtk.Switch(active=profile.get("adaptive_sync") is True))
        adaptive.set_sensitive(output.get("adaptive_sync") is not None)
        error = label()
        page.body.pack_start(error, False, False, 0)
        dialog.get_content_area().pack_start(page, True, True, 0)

        def response(widget, response_id):
            if response_id == Gtk.ResponseType.OK:
                try:
                    edited = {"name": profile["name"], "enabled": enabled.get_active(),
                              "mode": custom_mode.get_text().strip() if custom.get_active() else presets.get_active_id() or "",
                              "custom_mode": custom.get_active(),
                              "scale": scale.get_value(), "transform": transform.get_active_id(),
                              "position": {"x": int(coordinates[0].get_text()), "y": int(coordinates[1].get_text())},
                              "adaptive_sync": adaptive.get_active() if adaptive.get_sensitive() else None}
                    profiles = [edited if item["name"] == edited["name"] else item for item in self._profiles]
                    displays.validate(profiles)
                    displays.build_arguments(profiles, self._outputs)
                except (ValueError, TypeError) as exc:
                    error.set_text(str(exc))
                    return
                self._profiles, self._dirty = profiles, True
                self._save.set_sensitive(True)
                self._save.show()
                self._status.set_text("Changes not applied")
                self._render()
            widget.destroy()

        dialog.connect("response", response)
        dialog.show_all()
        # Gtk.Stack only selects visible children; restore the saved editor after
        # show_all() has made both the entry and the preset selector available.
        mode.set_visible_child_name("custom" if custom.get_active() else "preset")
        return dialog

    def _save_clicked(self, *_args):
        try:
            displays.validate(self._profiles)
            displays.build_arguments(self._profiles, self._outputs)
            displays.save_profiles(self._profiles)
            self._revision = displays.CONFIG_PATH.stat().st_mtime_ns
            self._saved_at = time.monotonic()
            self._dirty = False
            self._save.set_sensitive(False)
            self._save.hide()
            self._status.set_text("Saved. Applying display settings…")
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
