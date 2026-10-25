"""Shared status buttons and a compact control center for both shell layouts."""

import threading

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

import status_backend as backend
from shell_widgets import _prepare_launcher_dialog


class StatusControls:
    def __init__(self, parent, size, *, bottom_right=False):
        self.parent, self.size = parent, size
        self.bottom_right = bottom_right
        self.buttons = {}
        self.dialog = None
        self.data = {}
        self.busy = False
        self.alive = True
        self.pending = {}
        self.delays = {}
        self.error = ''
        self._syncing = False
        self._timer = GLib.timeout_add_seconds(3, self.refresh)
        parent.connect('destroy', self.close)
        self.refresh()

    def button(self, kind):
        button = Gtk.Button()
        button.get_style_context().add_class('system-status-button')
        image = Gtk.Image.new_from_icon_name('image-missing-symbolic', Gtk.IconSize.MENU)
        image.set_pixel_size(self.size)
        button.add(image)
        button.set_tooltip_text('Reading status…')
        button.connect('clicked', self.show)
        self.buttons[kind] = button
        self._update_buttons()
        return button

    def _update_buttons(self):
        for kind, button in self.buttons.items():
            state = self.data.get(kind)
            if state:
                button.get_child().set_from_icon_name(state['icon'], Gtk.IconSize.MENU)
                button.get_child().set_pixel_size(self.size)
                button.set_tooltip_text(state['text'])

    def refresh(self):
        if not self.alive:
            return False
        if self.busy:
            return True
        self.busy = True
        pending, self.pending = self.pending, {}

        def work():
            errors = []
            for operation in pending.values():
                try:
                    operation()
                except Exception as exc:
                    errors.append(str(exc))
            data = backend.snapshot()
            GLib.idle_add(done, data, '\n'.join(errors))

        def done(data, error):
            if not self.alive:
                return False
            self.busy = False
            self.data = data
            if pending:
                self.error = error
            self._update_buttons()
            if self.dialog:
                if not self._built:
                    self._build()
                self._sync()
            if self.pending:
                self.refresh()
            return False

        threading.Thread(target=work, daemon=True).start()
        return True

    def change(self, key, operation, debounce=False):
        if self._syncing:
            return
        if key in self.delays:
            GLib.source_remove(self.delays.pop(key))
        def commit():
            self.delays.pop(key, None)
            self.pending[key] = operation
            self.refresh()
            return False
        if debounce:
            self.delays[key] = GLib.timeout_add(200, commit)
        else:
            commit()

    def show(self, *_):
        if self.dialog:
            self.dialog.present()
            return
        self.dialog = Gtk.Dialog(title='Control Center', transient_for=self.parent,
                                 destroy_with_parent=True)
        self.dialog.add_button('Close', Gtk.ResponseType.CLOSE)
        self.dialog.connect('response', lambda *_: self.dialog.destroy())
        self.dialog.connect('destroy', self._dialog_closed)
        self.dialog.connect('key-press-event', self._key)
        _prepare_launcher_dialog(self.dialog, self.parent, 'yui-control-center')
        if self.bottom_right:
            GtkLayerShell.set_anchor(self.dialog, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_anchor(self.dialog, GtkLayerShell.Edge.BOTTOM, True)
            # The taskbar's exclusive zone already reserves the space below us.
            GtkLayerShell.set_margin(self.dialog, GtkLayerShell.Edge.RIGHT, 8)
            GtkLayerShell.set_margin(self.dialog, GtkLayerShell.Edge.BOTTOM, 8)
        width, height = 380, 540
        if self.parent.get_window():
            geometry = self.parent.get_display().get_monitor_at_window(self.parent.get_window()).get_geometry()
            width, height = min(width, geometry.width), min(height, geometry.height)
        self.dialog.set_default_size(width, height)
        self.dialog.set_size_request(min(width, 380), min(height, 540))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=12)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.body)
        self.dialog.get_content_area().pack_start(scroller, True, True, 0)
        self._built = False
        if self.data:
            self._build()
            self._sync()
        self.dialog.show_all()
        self.refresh()
        self.dialog.present()

    def _key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.dialog.destroy()
            return True
        return False

    def _dialog_closed(self, *_):
        self.dialog = None
        # Keep already requested adjustments, even if the window is closed.

    def _label(self, text):
        widget = Gtk.Label(label=text, xalign=0, wrap=True)
        widget.set_max_width_chars(36)
        self.body.pack_start(widget, False, False, 0)
        return widget

    def _switch(self, text, callback):
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Label(label=text, xalign=0), True, True, 0)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.pack_end(switch, False, False, 0)
        switch.connect('notify::active', lambda widget, *_: callback(widget.get_active()))
        self.body.pack_start(row, False, False, 0)
        return switch

    def _scale(self, text, minimum, callback):
        self._label(text)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, 100, 1)
        scale.set_digits(0)
        scale.set_hexpand(True)
        scale.set_size_request(-1, 36)
        scale.connect('value-changed', lambda widget: callback(round(widget.get_value())))
        self.body.pack_start(scale, False, False, 0)
        return scale

    def _build(self):
        self.volume = self._scale('Volume (%)', 0, lambda value:
                                  self.change('volume', lambda: backend.set_volume(value), True))
        self.mute = self._switch('Mute', lambda active:
                                 self.change('mute', lambda: backend.set_muted(active)))
        self.wifi = self._switch('Wi-Fi', lambda active:
                                 self.change('wifi', lambda: backend.set_wifi(active)))
        self.bluetooth = self._switch('Bluetooth', lambda active:
                                      self.change('bluetooth', lambda: backend.set_bluetooth(
                                          self.data.get('bt', {}).get('adapters', []), active)))
        self.brightness = {}
        for device in self.data.get('brightness', {}).get('devices', []):
            name = device['name']
            self.brightness[name] = self._scale(f'Brightness · {name} (%)', 1,
                lambda value, name=name: self.change('brightness:' + name,
                    lambda: backend.services.set_brightness(name, value), True))
        if not self.brightness:
            self._label('Brightness control unavailable')
        self.network_label = self._label('')
        self.battery_label = self._label('')
        self.status = self._label('')
        self._built = True
        self.body.show_all()

    def _sync(self):
        self._syncing = True
        try:
            for key, widget, value_key in [('volume', self.volume, 'volume'), ('mute', self.mute, 'muted'),
                                          ('wifi', self.wifi, 'wifi'), ('bluetooth', self.bluetooth, 'powered')]:
                category = {'volume': 'audio', 'mute': 'audio', 'wifi': 'network', 'bluetooth': 'bt'}[key]
                state = self.data.get(category, {})
                supported = value_key in state
                if key == 'wifi':
                    supported = supported and state.get('wireless', False)
                if key == 'bluetooth':
                    supported = supported and bool(state.get('adapters'))
                widget.set_sensitive(supported)
                if supported and key not in self.pending and key not in self.delays:
                    if isinstance(widget, Gtk.Scale):
                        if not widget.has_focus():
                            widget.set_value(state[value_key])
                    else:
                        widget.set_active(state[value_key])
            for device in self.data.get('brightness', {}).get('devices', []):
                widget = self.brightness.get(device['name'])
                key = 'brightness:' + device['name']
                if widget and key not in self.pending and key not in self.delays and not widget.has_focus():
                    widget.set_value(device['percent'])
            names = {device['name'] for device in self.data.get('brightness', {}).get('devices', [])}
            for name, widget in self.brightness.items():
                widget.set_sensitive(name in names)
            self.network_label.set_text(self.data.get('network', {}).get('text', ''))
            self.battery_label.set_text(self.data.get('pwr', {}).get('text', ''))
            errors = [state['text'] for state in self.data.values() if state.get('error')]
            self.status.set_text(self.error or '\n'.join(errors))
        finally:
            self._syncing = False

    def close(self, *_):
        self.alive = False
        GLib.source_remove(self._timer)
        for source in self.delays.values():
            GLib.source_remove(source)
        self.delays.clear()
        if self.dialog:
            self.dialog.destroy()
