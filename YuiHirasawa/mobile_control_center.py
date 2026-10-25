"""A mobile control-center shade pulled from the always-visible status bar."""

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from control_center import StatusControls


class MobileStatusControls(StatusControls):
    def __init__(self, parent, size):
        self._height = 1
        self._reveal = 0
        self._animation = None
        self._dragging = False
        self._eligible = False
        super().__init__(parent, size)
        # The status-bar surface alone receives the initial touch, even while
        # another application is active. Capture before the status buttons.
        self._pull = self._gesture(parent)
        self._pull.connect('drag-begin', self._pull_begin)
        self._pull.connect('drag-update', self._drag_update)
        self._pull.connect('drag-end', self._drag_end)
        self._pull.connect('cancel', self._drag_cancel)

    def _gesture(self, widget):
        gesture = Gtk.GestureDrag.new(widget)
        gesture.set_button(Gdk.BUTTON_PRIMARY)
        gesture.set_touch_only(False)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        return gesture

    def _create_dialog(self):
        super()._create_dialog()
        monitor = self.parent.get_display().get_monitor_at_window(self.parent.get_window())
        geometry = monitor.get_geometry()
        self._height = geometry.height
        GtkLayerShell.set_monitor(self.dialog, monitor)
        GtkLayerShell.set_exclusive_zone(self.dialog, -1)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self.dialog, edge, True)
        # Keep the original status-bar touch grab while the shade is moving.
        GtkLayerShell.set_keyboard_mode(self.dialog, GtkLayerShell.KeyboardMode.NONE)
        self.dialog.set_default_size(geometry.width, self._height)
        self.dialog.set_size_request(-1, self._height)
        self.dialog.resize(1, 1)
        self._dismiss = self._gesture(self.dialog)
        self._dismiss.connect('drag-begin', self._dismiss_begin)
        self._dismiss.connect('drag-update', self._drag_update)
        self._dismiss.connect('drag-end', self._drag_end)
        self._dismiss.connect('cancel', self._drag_cancel)

    def show(self, *_):
        if not self.dialog:
            self._create_dialog()
            self._position(0)
            self.dialog.show_all()
            self.refresh()
        self._settle(True)

    def _stop_animation(self):
        if self._animation is not None:
            GLib.source_remove(self._animation)
            self._animation = None

    def _position(self, reveal):
        self._reveal = max(0, min(self._height, reveal))
        if self.dialog:
            GtkLayerShell.set_margin(self.dialog, GtkLayerShell.Edge.TOP,
                                     round(self._reveal - self._height))

    def _pull_begin(self, _gesture, _x, y):
        self._eligible = 0 <= y <= self.parent.HEIGHT
        self._dragging = False
        self._origin = 0

    def _dismiss_begin(self, _gesture, _x, y):
        # Leave scrolling and sliders alone; dismiss from the top/bottom edge.
        self._eligible = y <= 48 or y >= self._height - 48
        self._dragging = False
        self._origin = self._reveal

    def _drag_update(self, gesture, dx, dy):
        if not self._eligible:
            return
        if not self._dragging:
            direction_ok = dy > 0 if self._origin == 0 else dy < 0
            if not direction_ok or abs(dy) < 10 or abs(dy) <= abs(dx):
                return
            self._dragging = True
            self._stop_animation()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if not self.dialog:
                self._create_dialog()
                self._position(dy)
                self.dialog.show_all()
                self.refresh()
            GtkLayerShell.set_keyboard_mode(self.dialog, GtkLayerShell.KeyboardMode.NONE)
        self._position(self._origin + dy)

    def _drag_end(self, _gesture, _dx, dy):
        if self._dragging:
            # A deliberate short pull opens; a short upward pull closes.
            threshold = max(48, self._height * 0.12)
            opened = dy >= threshold if self._origin == 0 else dy > -threshold
            self._settle(opened)
        self._dragging = False
        self._eligible = False

    def _drag_cancel(self, _gesture, _sequence):
        if self._dragging:
            self._settle(self._origin > 0)
        self._dragging = False
        self._eligible = False

    def _settle(self, opened):
        self._stop_animation()
        self._target = self._height if opened else 0
        self._start = self._reveal
        self._started = GLib.get_monotonic_time()
        self._animation = GLib.timeout_add(16, self._animate)

    def _animate(self):
        if not self.dialog:
            self._animation = None
            return GLib.SOURCE_REMOVE
        progress = min(1, (GLib.get_monotonic_time() - self._started) / 220_000)
        self._position(self._start + (self._target - self._start) * (1 - (1 - progress) ** 3))
        if progress < 1:
            return GLib.SOURCE_CONTINUE
        self._animation = None
        if self._target == 0:
            self.dialog.destroy()
        else:
            GtkLayerShell.set_keyboard_mode(self.dialog, GtkLayerShell.KeyboardMode.ON_DEMAND)
            self.dialog.present()
        return GLib.SOURCE_REMOVE

    def _dialog_closed(self, *_):
        self._stop_animation()
        self._reveal = 0
        self._dragging = False
        super()._dialog_closed()
