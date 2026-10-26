"""Lille theft protection shared by the mobile and desktop shells."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import re
import subprocess
import threading
import time

from settings import DEFAULT_CONFIG, load_config, save_config

LOGGER = logging.getLogger('yui.lille')
MESSAGE = 'This device has been disabled and is being tracked. Local authorities will be alerted.'


def charger_connected(root=Path('/sys/class/power_supply')) -> bool | None:
    """Read external power, including fully charged batteries; None is unknown."""
    states = []
    uncertain = False
    try:
        supplies = list(root.iterdir())
    except OSError:
        return None
    for supply in supplies:
        try:
            if (supply / 'type').read_text().strip() == 'Battery':
                continue
            scope = supply / 'scope'
            if scope.exists() and scope.read_text().strip() == 'Device':
                continue  # A connected mouse/controller is not the host charger.
            online = supply / 'online'
            if not online.exists():
                continue
            value = online.read_text().strip()
            if value not in ('0', '1', '2'):
                uncertain = True
            else:
                states.append(value != '0')  # Some supplies report 2 for online.
        except OSError:
            uncertain = True
    if any(states):
        return True
    if uncertain or not states:
        return None
    return False


def session_state_path() -> Path:
    session = (os.environ.get('YUI_LILLE_SESSION') or os.environ.get('XDG_SESSION_ID')
               or os.environ.get('WAYLAND_DISPLAY') or 'standalone')
    token = hashlib.sha256(session.encode()).hexdigest()[:24]
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/tmp/yui-{os.getuid()}')
    return runtime / f'yui-lille-{token}.json'


def protection_triggered() -> bool:
    try:
        data = json.loads(session_state_path().read_text())
        return data.get('triggered') is True and data.get('disable_screen', True) is True
    except (OSError, ValueError, AttributeError):
        return False


def split_wifi_row(line):
    """Decode nmcli's escaped colons and backslashes without losing SSID text."""
    fields, field, escaped = [], [], False
    for character in line:
        if escaped:
            field.append(character)
            escaped = False
        elif character == chr(92):
            escaped = True
        elif character == ':':
            fields.append(''.join(field))
            field = []
        else:
            field.append(character)
    if escaped:
        raise ValueError('Incomplete nmcli escape')
    fields.append(''.join(field))
    return fields


def scan_networks():
    """Return visible (SSID, BSSID) pairs, or None when scanning is unavailable."""
    def run(*args):
        result = subprocess.run(['nmcli', '--colors', 'no', '--wait', '10', *args],
                                capture_output=True, text=True, timeout=15,
                                env={**os.environ, 'LC_ALL': 'C'}, check=False)
        if result.returncode:
            raise ValueError('NetworkManager scan failed')
        return result.stdout
    try:
        if run('radio', 'wifi').strip() != 'enabled':
            return None
        output = run('--terse', '--escape', 'yes', '--fields', 'SSID,BSSID',
                     'device', 'wifi', 'list', '--rescan', 'yes')
        rows = []
        for line in output.splitlines():
            row = split_wifi_row(line)
            if len(row) != 2 or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', row[1]):
                raise ValueError('Invalid NetworkManager access point')
            rows.append((row[0], row[1].upper()))
        return rows
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def trusted_nearby(rows, ssids, bssids):
    if rows is None:
        return None
    trusted_bssids = {value.upper() for value in bssids}
    return any(ssid in ssids or bssid.upper() in trusted_bssids for ssid, bssid in rows)


class NetworkProbe:
    """Keep blocking Wi-Fi scans off GTK's main loop; discard stale results."""
    def __init__(self, ssids, bssids, scanner=scan_networks, clock=time.monotonic):
        self.ssids, self.bssids = ssids, bssids
        self.scanner, self.clock = scanner, clock
        self._lock = threading.Lock()
        self._running = False
        self._next_scan = 0
        self._updated = None
        self._nearby = None

    def read(self):
        now = self.clock()
        with self._lock:
            if not self._running and now >= self._next_scan:
                self._running = True
                threading.Thread(target=self._scan, daemon=True).start()
            return self._nearby if self._updated is not None and now - self._updated <= 15 else None

    def _scan(self):
        try:
            nearby = trusted_nearby(self.scanner(), self.ssids, self.bssids)
        except Exception:
            LOGGER.exception('Lille Wi-Fi scan failed')
            nearby = None
        with self._lock:
            self._nearby = nearby
            self._updated = self.clock()
            self._next_scan = self._updated + 5
            self._running = False


class LilleState:
    """Session latch with proximity arming, a continuous delay, and opt-in persistence."""
    def __init__(self, path, *, owner, location, connected, config=None,
                 clock=time.monotonic, persist=None):
        self.path = Path(path)
        self.owner = owner
        self.location = location
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.clock = clock
        self.persist = persist or self._persist_trip
        self.uses_networks = bool(self.config['lille_ssids'] or self.config['lille_bssids'])
        self.disable_screen = self.config['lille_disable_screen']
        self.armed = False
        self.triggered = bool(self.config['lille_persist_trip'] and self.config['lille_trip_value'])
        self._persisted = self.triggered
        self._pending_since = None
        loaded = self.refresh()
        if owner:
            if not loaded:
                self.armed = not self.uses_networks and connected is True
            self._save()

    def refresh(self):
        try:
            data = json.loads(self.path.read_text())
            if (not isinstance(data, dict) or not isinstance(data.get('armed'), bool)
                    or not isinstance(data.get('triggered'), bool)):
                return False
            self.armed = data['armed']
            self.triggered = self.triggered or data['triggered']
            self.disable_screen = data.get('disable_screen', self.disable_screen) is True
            return True
        except (OSError, ValueError):
            return False

    def update(self, connected, nearby=None):
        self.refresh()
        if self.owner and not self.triggered:
            if self.uses_networks and nearby is True and not self.armed:
                self.armed = True
                self._save()
            condition = (self.armed and nearby is False
                         and (not self.config['lille_require_charger'] or connected is False)
                         if self.uses_networks else self.armed and connected is False)
            if condition:
                now = self.clock()
                if self._pending_since is None:
                    self._pending_since = now
                if now - self._pending_since >= self.config['lille_delay_seconds']:
                    self.triggered = True
                    self._save()
                    LOGGER.warning('Lille tripped')
            else:
                self._pending_since = None
        if self.owner and self.triggered and self.config['lille_persist_trip'] and not self._persisted:
            self.persist()
            self._persisted = True
        return self.triggered

    @staticmethod
    def _persist_trip():
        config = load_config()
        config['lille_trip_value'] = True
        save_config(config)

    def _save(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent,
                                             prefix='.yui-lille-', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'armed': self.armed, 'triggered': self.triggered,
                           'disable_screen': self.disable_screen}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class LilleProtection:
    """Evaluate Lille rules and optionally hide Yui windows across workers."""
    def __init__(self, *, owner=True, config=None, state_path=None, reader=charger_connected):
        self.config = load_config() if config is None else {**DEFAULT_CONFIG, **config}
        self.owner = owner
        self.reader = reader
        self.state = None
        self.overlays = {}
        self._source = None
        self._hook = None
        self._display_handlers = []
        self._css = None
        if not self.config.get('enable_lille', False):
            return
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('GtkLayerShell', '0.1')
        from gi.repository import Gdk, GLib, GObject, Gtk, GtkLayerShell, Pango
        self.Gdk, self.GLib, self.GObject = Gdk, GLib, GObject
        self.Gtk, self.layers = Gtk, GtkLayerShell
        self.Pango = Pango
        self.display = Gdk.Display.get_default()
        self.state = LilleState(state_path or session_state_path(), owner=owner,
                               location=self.config['lille_location'],
                               connected=reader() if owner else None, config=self.config)
        self._network_probe = (NetworkProbe(self.config['lille_ssids'], self.config['lille_bssids'])
                               if owner and self.state.uses_networks and not self.state.triggered else None)
        self._map_signal = GObject.signal_lookup('map', Gtk.Widget)
        self._hook = GObject.add_emission_hook(Gtk.Widget, 'map', self._mapped)
        self._source = GLib.timeout_add(250, self._poll)
        self._initial = GLib.idle_add(self._initial_poll)

    def _initial_poll(self):
        self._initial = None
        self._poll()
        return self.GLib.SOURCE_REMOVE

    def _mapped(self, window, *_args):
        if isinstance(window, self.Gtk.Window) and window not in self.overlays.values():
            self.state.refresh()
            if self.state.triggered and self.state.disable_screen:
                window.hide()
        return True

    def _poll(self):
        was_triggered = self.state.triggered
        try:
            nearby = self._network_probe.read() if self._network_probe is not None else None
            triggered = self.state.update(self.reader() if self.owner else None, nearby)
        except OSError:
            LOGGER.exception('Could not persist Lille state')
            triggered = self.state.triggered
        if triggered and self.state.disable_screen:
            for window in self.Gtk.Window.list_toplevels():
                if window not in self.overlays.values():
                    window.hide()
            if self.owner:
                if not was_triggered or not self._display_handlers:
                    self._display_handlers = [
                        self.display.connect(signal, self._sync_monitors)
                        for signal in ('monitor-added', 'monitor-removed')]
                self._sync_monitors()
        return self.GLib.SOURCE_CONTINUE

    def _sync_monitors(self, *_args):
        monitors = [self.display.get_monitor(i) for i in range(self.display.get_n_monitors())]
        for monitor in list(self.overlays):
            if monitor not in monitors:
                self.overlays.pop(monitor).destroy()
        for monitor in monitors:
            if monitor in self.overlays:
                continue
            window = self.Gtk.Window(title='Lille')
            self.overlays[monitor] = window
            window.set_decorated(False)
            window.connect('delete-event', lambda *_: True)
            window.connect('key-press-event', lambda *_: True)
            self.layers.init_for_window(window)
            self.layers.set_monitor(window, monitor)
            self.layers.set_layer(window, self.layers.Layer.OVERLAY)
            self.layers.set_namespace(window, 'yui-lille')
            for edge in (self.layers.Edge.TOP, self.layers.Edge.BOTTOM,
                         self.layers.Edge.LEFT, self.layers.Edge.RIGHT):
                self.layers.set_anchor(window, edge, True)
            self.layers.set_exclusive_zone(window, -1)
            self.layers.set_keyboard_mode(window, self.layers.KeyboardMode.EXCLUSIVE)
            window.get_style_context().add_class('lille')
            if self._css is None:
                self._css = self.Gtk.CssProvider()
                self._css.load_from_data(b'''
                window.lille { background: #111; color: white; }
                .lille label { color: white; }
                .lille .lille-title { font-size: 28px; font-weight: bold; }
                .lille .lille-body { font-size: 18px; }
                ''')
                self.Gtk.StyleContext.add_provider_for_screen(
                    window.get_screen(), self._css, self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            # Overlay children do not contribute to the window's preferred
            # size. Otherwise wrapping at a narrow minimum width can request
            # a surface taller than the output and put its center offscreen.
            frame = self.Gtk.Overlay()
            frame.add(self.Gtk.DrawingArea())
            content = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=24)
            content.set_border_width(24)
            content.set_halign(self.Gtk.Align.CENTER)
            content.set_valign(self.Gtk.Align.CENTER)
            title = self.Gtk.Label(label=f"Please return to {self.config['lille_location']}")
            title.get_style_context().add_class('lille-title')
            body = self.Gtk.Label(label=MESSAGE)
            body.get_style_context().add_class('lille-body')
            for label in (title, body):
                label.set_line_wrap(True)
                label.set_line_wrap_mode(self.Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(48)
                label.set_justify(self.Gtk.Justification.CENTER)
                content.pack_start(label, False, False, 0)
            frame.add_overlay(content)
            frame.connect('get-child-position', self._position_message)
            window.add(frame)
            # GDK geometry is already in logical pixels, including on scaled
            # outputs. Let layer-shell's four anchors supply the actual size.
            geometry = monitor.get_geometry()
            window.set_default_size(max(1, geometry.width), max(1, geometry.height))
            window.show_all()

    def _position_message(self, frame, content, allocation):
        width, height = frame.get_allocated_width(), frame.get_allocated_height()
        allocation.width = max(1, min(720, width))
        _minimum, natural = content.get_preferred_height_for_width(allocation.width)
        allocation.height = max(1, natural)
        allocation.x = (width - allocation.width) // 2
        allocation.y = (height - allocation.height) // 2
        return True

    def close(self):
        if self.state is None:
            return
        for source in (self._source, self._initial):
            if source is not None:
                self.GLib.source_remove(source)
        self._source = self._initial = None
        if self._hook is not None:
            self.GObject.signal_remove_emission_hook(self._map_signal, self._hook)
            self._hook = None
        for handler in self._display_handlers:
            self.display.disconnect(handler)
        self._display_handlers.clear()
        for window in list(self.overlays.values()):
            window.destroy()
        self.overlays.clear()
        if self._css is not None:
            self.Gtk.StyleContext.remove_provider_for_screen(self.display.get_default_screen(), self._css)
            self._css = None
