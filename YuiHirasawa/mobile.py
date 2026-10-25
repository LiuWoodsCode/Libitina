"""Mobile shell layout and interactions."""

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell, Pango

from backend import (
    ApplicationCatalog,
    KeyboardVisibilityMonitor,
    WaylandWindowCollector,
    WindowInfo,
)
from mobile_control_center import MobileStatusControls
from launcher_control import LauncherControl
from app_launch import launch_application, launch_in_foot
from shell_widgets import SystemTray, _fixed_icon_image, show_error_dialog, attach_application_menu
from wallpaper import Wallpaper


class ApplicationLauncher(Gtk.Window, ApplicationCatalog):
    SWIPE_LAUNCH_COOLDOWN_US = 3_000_000
    ICON_SIZE = 72
    ICON_SPACING = 18
    GRID_MARGIN = 18

    def __init__(self):
        super().__init__(title="Applications")
        self._locked_open = False
        self.set_decorated(False)
        self.set_resizable(True)
        self.get_style_context().add_class("application-launcher")
        self.connect("key-press-event", self._on_key_press)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_exclusive_zone(self, 0)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_namespace(self, "application-launcher")

        overlay = Gtk.Overlay()
        self._wallpaper_image = Wallpaper()
        overlay.add(self._wallpaper_image)
        overlay.add_overlay(self._build_content())
        self.add(overlay)

    def _build_content(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.get_style_context().add_class("launcher")

        self._apps = self._applications()
        self._tile_width = None
        self._tile_height = None
        self._grid_shape = None
        self._current_page = 0
        self._page_dots = []
        self._rebuild_source = None
        self._page_width = 1
        self._page_height = 1
        self._page_widgets = []
        self._snap_source = None
        self._page_drag_claimed = False
        self._page_drag_moved = False
        self._launch_blocked_until = 0
        # A scrollable strip keeps both neighboring pages visible during a drag.
        self._pages = Gtk.Layout()
        self._page_adjustment = self._pages.get_hadjustment()
        self.connect("unmap", self._on_pager_unmap)
        self._pages.connect("size-allocate", self._on_grid_allocate)
        outer.pack_start(self._pages, True, True, 0)

        self._dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._dots.set_halign(Gtk.Align.CENTER)
        self._dots.set_size_request(-1, 28)
        outer.pack_start(self._dots, False, False, 0)
        outer.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        outer.connect("scroll-event", self._on_page_scroll)
        # Capture on the window so buttons cannot consume the touch sequence
        # and drags on the empty (windowless) grid background also reach us.
        self._swipe = Gtk.GestureDrag.new(self)
        self._swipe.set_button(Gdk.BUTTON_PRIMARY)
        self._swipe.set_touch_only(False)
        self._swipe.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._swipe.connect("drag-begin", self._on_page_drag_begin)
        self._swipe.connect("drag-update", self._on_page_drag_update)
        self._swipe.connect("drag-end", self._on_page_drag_end)
        self._swipe.connect("cancel", self._on_page_drag_cancel)
        return outer

    def _on_grid_allocate(self, _widget, allocation) -> None:
        if self._tile_width is None:
            self._tile_width = self._tile_height = self.ICON_SIZE
            if self._apps:
                # Measure under the launcher after its CSS has been installed.
                tile = self._app_tile(self._apps[0])
                self._pages.put(tile, 0, 0)
                tile.show_all()
                _minimum, natural = tile.get_preferred_size()
                self._tile_width = max(1, natural.width)
                self._tile_height = max(1, natural.height)
                tile.destroy()
        # Fit as many naturally sized tiles as the available space allows.
        spacing = self.ICON_SPACING
        margin = 2 * self.GRID_MARGIN
        shape = (max(1, (allocation.width - margin + spacing) // (self._tile_width + spacing)),
                 max(1, (allocation.height - margin + spacing) // (self._tile_height + spacing)))
        size_changed = (allocation.width, allocation.height) != (self._page_width, self._page_height)
        self._page_width = max(1, allocation.width)
        self._page_height = max(1, allocation.height)
        if shape == self._grid_shape:
            if size_changed:
                self._layout_pages()
                self._set_page(self._current_page, animate=False)
            return
        self._grid_shape = shape
        if self._rebuild_source is None:
            self._rebuild_source = GLib.idle_add(self._rebuild_pages)

    def _rebuild_pages(self) -> bool:
        self._rebuild_source = None
        self._stop_page_snap()
        self._page_drag_claimed = False
        cols, rows = self._grid_shape
        capacity = cols * rows
        # Keep the first app on the current page in view after a resize.
        first_app = self._current_page * getattr(self, "_page_capacity", capacity)
        self._page_capacity = capacity
        for child in self._pages.get_children():
            child.destroy()
        for child in self._dots.get_children():
            child.destroy()
        self._page_dots = []
        self._page_widgets = []
        count = max(1, (len(self._apps) + capacity - 1) // capacity)
        for page_index in range(count):
            grid = Gtk.Grid(column_spacing=self.ICON_SPACING, row_spacing=self.ICON_SPACING)
            grid.set_halign(Gtk.Align.CENTER)
            grid.set_valign(Gtk.Align.START)
            grid.set_border_width(self.GRID_MARGIN)
            grid.set_column_homogeneous(True)
            # Reserve the full page width so sparse pages stay aligned.
            grid.set_size_request(
                2 * self.GRID_MARGIN + cols * self._tile_width + (cols - 1) * self.ICON_SPACING, -1)
            start = page_index * capacity
            for index, app in enumerate(self._apps[start:start + capacity]):
                grid.attach(self._app_tile(app), index % cols, index // cols, 1, 1)
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            page.pack_start(grid, False, False, 0)
            self._pages.put(page, page_index * self._page_width, 0)
            self._page_widgets.append(page)
            dot = Gtk.Button(label="●")
            dot.get_style_context().add_class("page-dot")
            dot.set_tooltip_text(f"Page {page_index + 1} of {count}")
            dot.connect("clicked", lambda _button, i=page_index: self._set_page(i))
            self._dots.pack_start(dot, False, False, 0)
            self._page_dots.append(dot)
        self._pages.show_all()
        self._dots.show_all()
        self._layout_pages()
        self._set_page(min(first_app // capacity, count - 1), animate=False)
        return GLib.SOURCE_REMOVE

    def _layout_pages(self) -> None:
        width, height = self._page_width, self._page_height
        self._pages.set_size(max(1, len(self._page_widgets)) * width, height)
        for index, page in enumerate(self._page_widgets):
            page.set_size_request(width, height)
            self._pages.move(page, index * width, 0)
        self._page_adjustment.configure(
            self._page_adjustment.get_value(), 0,
            max(1, len(self._page_widgets)) * width, 1, width, width)

    def _stop_page_snap(self) -> None:
        if self._snap_source is not None:
            GLib.source_remove(self._snap_source)
            self._snap_source = None

    def _on_pager_unmap(self, _widget) -> None:
        self._page_drag_claimed = False
        self._set_page(self._current_page, animate=False)

    def _set_page(self, index: int, *, animate: bool = True) -> None:
        if not self._page_dots:
            return
        self._current_page = max(0, min(index, len(self._page_dots) - 1))
        self._stop_page_snap()
        target = self._current_page * self._page_width
        start = self._page_adjustment.get_value()
        if animate and abs(target - start) > 0.5:
            self._snap_start = start
            self._snap_target = target
            self._snap_started = GLib.get_monotonic_time()
            self._snap_source = GLib.timeout_add(16, self._animate_page_snap)
        else:
            self._page_adjustment.set_value(target)
        for i, dot in enumerate(self._page_dots):
            style = dot.get_style_context()
            (style.add_class if i == self._current_page else style.remove_class)("active")

    def _animate_page_snap(self) -> bool:
        progress = min(1.0, (GLib.get_monotonic_time() - self._snap_started) / 220_000)
        eased = 1 - (1 - progress) ** 3
        self._page_adjustment.set_value(
            self._snap_start + (self._snap_target - self._snap_start) * eased)
        if progress >= 1:
            self._snap_source = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _on_page_scroll(self, _widget, event) -> bool:
        if event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            delta = 1
        elif event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            delta = -1
        else:
            # Accumulate touchpad deltas so one gesture does not skip every page.
            success, dx, dy = event.get_scroll_deltas()
            if not success:
                return False
            self._scroll_delta = getattr(self, "_scroll_delta", 0) + (dx if abs(dx) > abs(dy) else dy)
            if abs(self._scroll_delta) < 1:
                return True
            delta = 1 if self._scroll_delta > 0 else -1
            self._scroll_delta = 0
        self._set_page(self._current_page + delta)
        return True

    def _on_page_drag_begin(self, _gesture, _x: float, _y: float) -> None:
        self._stop_page_snap()
        self._page_drag_claimed = False
        self._page_drag_moved = False
        self._drag_start_position = self._page_adjustment.get_value()
        self._drag_start_page = min(len(self._page_dots) - 1,
                                    int(self._drag_start_position / self._page_width + 0.5))

    def _on_page_drag_update(self, gesture, dx: float, dy: float) -> None:
        if max(abs(dx), abs(dy)) >= 10:
            self._page_drag_moved = True
            # Block before release too, in case clicked precedes drag-end.
            self._start_swipe_launch_cooldown()
        if not self._page_drag_claimed and abs(dx) >= 10 and abs(dx) > abs(dy):
            # Claim before release, cancelling any app button's pending click.
            self._page_drag_claimed = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self._page_drag_claimed:
            limit = max(0, len(self._page_dots) - 1) * self._page_width
            self._page_adjustment.set_value(
                max(0, min(self._drag_start_position - dx, limit)))

    def _on_page_drag_end(self, _gesture, dx: float, dy: float) -> None:
        if self._page_drag_moved or self._page_drag_claimed or max(abs(dx), abs(dy)) >= 10:
            self._start_swipe_launch_cooldown()
        self._page_drag_moved = False
        if self._page_drag_claimed:
            target = self._drag_start_page
            if abs(dx) >= max(48, self._page_width * 0.2):
                target += 1 if dx < 0 else -1
            self._set_page(target)
        else:
            # A tap can interrupt an in-progress snap; finish settling it.
            self._set_page(self._current_page)
        self._page_drag_claimed = False

    def _on_page_drag_cancel(self, _gesture, _sequence) -> None:
        if self._page_drag_moved or self._page_drag_claimed:
            self._start_swipe_launch_cooldown()
        self._page_drag_moved = False
        self._page_drag_claimed = False
        self._set_page(self._current_page)

    def _start_swipe_launch_cooldown(self) -> None:
        self._launch_blocked_until = GLib.get_monotonic_time() + self.SWIPE_LAUNCH_COOLDOWN_US

    def _app_tile(self, app: Gio.DesktopAppInfo) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("application-button")
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_halign(Gtk.Align.CENTER)
        button.set_valign(Gtk.Align.START)
        button.set_tooltip_text(app.get_display_name())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        icon = app.get_icon()
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DIALOG) if icon else \
            Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        image.set_pixel_size(self.ICON_SIZE)
        content.pack_start(image, False, False, 0)
        label = Gtk.Label(label=app.get_display_name())
        label.set_justify(Gtk.Justification.CENTER)
        # Let the icon determine tile width; truncate names within that width.
        label.set_max_width_chars(1)
        label.set_width_chars(1)
        label.set_single_line_mode(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        content.pack_start(label, False, False, 0)
        button.add(content)
        button.connect("clicked", self._launch_app, app)
        attach_application_menu(button, self, app, self._launch_app)
        return button

    def _launch_app(self, _button: Gtk.Button, app: Gio.DesktopAppInfo) -> None:
        if (self._page_drag_moved or self._page_drag_claimed
                or GLib.get_monotonic_time() < self._launch_blocked_until):
            return
        if launch_application(
            app, lambda title, detail: self._show_error(title, detail, app),
            self.get_display().get_app_launch_context()
        ):
            if not self._locked_open:
                self.hide()

    def _show_error(self, title: str, detail: str, app=None) -> None:
        retry = (
            lambda: launch_in_foot(app, self.get_display().get_app_launch_context())
        ) if app is not None else None
        if show_error_dialog(self, title, detail, on_retry=retry):
            if not self._locked_open:
                self.hide()

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if not self._locked_open:
                self.hide()
            return True
        if event.keyval in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            self._set_page(self._current_page + (1 if event.keyval == Gdk.KEY_Page_Down else -1))
            return True
        return False

    def set_locked_open(self, locked: bool) -> None:
        self._locked_open = locked
        if locked:
            self.show_all()
            self.present()

    def toggle(self) -> None:
        if self._locked_open:
            return
        if self.get_visible():
            self.hide()
        else:
            self.show_all()
            self.present()


class StatusBar(Gtk.Window):
    HEIGHT = 16
    STATUS_ICON_SIZE = 16

    def __init__(self):
        super().__init__(title="Status Bar")
        self.set_decorated(False)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_exclusive_zone(self, self.HEIGHT)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(self, "wlroots-statusbar")

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._clock = Gtk.Label()
        self._clock.get_style_context().add_class("clock")
        bar.pack_start(self._clock, False, False, 0)

        status_icons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._status_controls = MobileStatusControls(self, self.STATUS_ICON_SIZE)
        for kind in ("network", "bt", "audio", "pwr"):
            status_icons.pack_start(self._status_controls.button(kind), False, False, 0)
        bar.pack_end(status_icons, False, False, 0)
        self.add(bar)

        self.set_size_request(-1, self.HEIGHT)
        self.resize(1, 1)
        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)

    def _update_clock(self) -> bool:
        now = GLib.DateTime.new_now_local()
        self._clock.set_text(now.format("%H:%M"))
        self._clock.set_tooltip_text(now.format("%A, %B %e, %Y"))
        return True


class RunningWindow(Gtk.Window, ApplicationCatalog):
    ICON_SIZE = 32

    def __init__(self, on_running_changed: Callable[[bool], None]):
        super().__init__(title="Running Applications")
        self._wayland = WaylandWindowCollector()
        self._windows: dict[str, WindowInfo] = {}
        self._buttons: dict[str, Gtk.Button] = {}
        self._swipe_rows: dict[str, Gtk.Layout] = {}
        self._swipe_offsets: dict[str, float] = {}
        self._swipe_animations: dict[str, int] = {}
        self._long_press_gestures: dict[str, Gtk.GestureLongPress] = {}
        self._close_swipes: dict[str, Gtk.GestureDrag] = {}
        self._swiping_windows: set[str] = set()
        self._button_signatures: dict[str, tuple[str, str]] = {}
        self._suppress_clicks: set[str] = set()
        self._pending_long_presses: set[str] = set()
        self._active_window_menu: Gtk.Menu | None = None
        self._desktop_icon_cache: dict[tuple[str, str], tuple[str, str]] = {}
        self._has_running: bool | None = None
        self._on_running_changed = on_running_changed

        self.set_default_size(480, 420)
        self.connect("delete-event", self._on_delete)

        self._install_css()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        self._task_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._task_box.set_hexpand(True)
        self._task_box.set_valign(Gtk.Align.START)
        scroller.add(self._task_box)
        content.pack_start(scroller, True, True, 0)

        self._tray = SystemTray()
        self._tray.set_halign(Gtk.Align.END)
        content.pack_end(self._tray, False, False, 0)
        self.add(content)

        GLib.timeout_add(100, self._refresh_tick)

    def _on_delete(self, *_args) -> bool:
        self.hide()
        return True

    def toggle(self) -> None:
        if self.get_visible():
            self.hide()
        else:
            self.show_all()
            self.present()

    def _install_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
        button.taskbar-button { min-height: 44px; padding: 4px 8px; }
        button.taskbar-button label.window-title { font-weight: bold; }
        button.taskbar-button label.application-name { font-size: 0.85em; opacity: 0.72; }
        button.tray-button { min-width: 32px; min-height: 12px; padding: 0; }
        button.system-status-button { min-width: 20px; min-height: 0; padding: 0; border-width: 0; }
        .clock { min-width: 48px; min-height: 0; padding: 0 6px 0 1px; }
        button.launch-button { min-height: 28px; padding: 1px 16px; }
        .launcher { padding: 0; }
        window.application-launcher .launcher,
        window.application-launcher scrolledwindow,
        window.application-launcher viewport,
        window.application-launcher flowbox,
        window.application-launcher layout {
            background-color: transparent;
            background-image: none;
        }
        .application-grid { padding: 8px; }
        .application-grid flowboxchild { padding: 0; }
        window.application-launcher button.application-button {
            min-width: 0; min-height: 0; padding: 0;
            border: none; border-radius: 12px;
            background-color: transparent; background-image: none; box-shadow: none;
        }
        window.application-launcher button.application-button:hover,
        window.application-launcher button.application-button:focus,
        window.application-launcher button.application-button:active {
            background-color: rgba(255, 255, 255, 0.18);
        }
        window.application-launcher button.application-button label {
            color: white; font-size: 9pt; text-shadow: 0 1px 2px black;
        }
        window.application-launcher button.page-dot {
            min-width: 16px; min-height: 20px; padding: 0;
            border: none; background: transparent; box-shadow: none;
            color: rgba(255, 255, 255, 0.45);
        }
        window.application-launcher button.page-dot.active { color: white; }
        """)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _refresh_tick(self) -> bool:
        windows = [
            window for window in self._wayland.windows()
            if window.title != self.get_title()
        ]
        windows.sort(key=lambda window: (
            (window.app_id or window.title).casefold(),
            window.title.casefold(),
            window.key,
        ))
        self._set_windows(windows)
        return True

    def _set_windows(self, windows: list[WindowInfo]) -> None:
        windows_by_key = {window.key: window for window in windows}
        old, new = set(self._buttons), set(windows_by_key)
        for key in old - new:
            self._stop_close_swipe_animation(key)
            self._task_box.remove(self._swipe_rows.pop(key))
            self._buttons.pop(key)
            self._swipe_offsets.pop(key, None)
            self._long_press_gestures.pop(key, None)
            self._close_swipes.pop(key, None)
            self._swiping_windows.discard(key)
            self._button_signatures.pop(key, None)
            self._suppress_clicks.discard(key)
            self._pending_long_presses.discard(key)
        for window in windows:
            key = window.key
            if key in self._buttons:
                continue
            button = Gtk.Button()
            button.get_style_context().add_class("taskbar-button")
            button.connect("clicked", self._on_clicked, key)
            button.connect("button-press-event", self._on_button_press, key)
            gesture = Gtk.GestureLongPress.new(button)
            gesture.set_touch_only(False)
            gesture.set_button(Gdk.BUTTON_PRIMARY)
            gesture.connect("pressed", self._on_long_press, key)
            gesture.connect("end", self._on_long_press_end, key)
            self._buttons[key] = button
            self._long_press_gestures[key] = gesture
            row = Gtk.Layout()
            row.set_hexpand(True)
            row.put(button, 0, 0)
            row.connect("size-allocate", self._on_swipe_row_allocate, key)
            self._swipe_rows[key] = row
            # Capture on the stationary row so movement remains relative to
            # the original touch position while its button follows the finger.
            swipe = Gtk.GestureDrag.new(row)
            swipe.set_button(Gdk.BUTTON_PRIMARY)
            swipe.set_touch_only(False)
            swipe.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            swipe.connect("drag-update", self._on_close_swipe_update, key)
            swipe.connect("drag-end", self._on_close_swipe_end, key)
            swipe.connect("cancel", self._on_close_swipe_cancel, key)
            self._close_swipes[key] = swipe
            self._task_box.pack_start(row, False, False, 0)
        self._windows = windows_by_key
        for position, window in enumerate(windows):
            button = self._buttons[window.key]
            self._task_box.reorder_child(self._swipe_rows[window.key], position)
            self._update_button(button, window)
            _minimum, height = button.get_preferred_height()
            self._swipe_rows[window.key].set_size_request(-1, height)
        self._task_box.show_all()
        has_running = bool(windows)
        if has_running != self._has_running:
            self._has_running = has_running
            self._on_running_changed(has_running)

    def _update_button(self, button: Gtk.Button, window: WindowInfo) -> None:
        signature = (window.title, window.app_id)
        if self._button_signatures.get(window.key) != signature:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon = self._window_icon_image(window)
            if icon is not None:
                row.pack_start(icon, False, False, 0)

            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            names.set_hexpand(True)
            title = Gtk.Label(label=window.title or "Window")
            title.set_xalign(0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.get_style_context().add_class("window-title")
            names.pack_start(title, False, False, 0)
            if window.app_id:
                application = Gtk.Label(label=window.app_id)
                application.set_xalign(0)
                application.set_ellipsize(Pango.EllipsizeMode.END)
                application.get_style_context().add_class("application-name")
                names.pack_start(application, False, False, 0)
            row.pack_start(names, True, True, 0)

            old = button.get_child()
            if old is not None:
                button.remove(old)
            button.add(row)
            button.set_tooltip_text(window.title)
            self._button_signatures[window.key] = signature
            button.show_all()
        style = button.get_style_context()
        (style.add_class if window.active else style.remove_class)("active")

    def _close_swipe_distance(self, button: Gtk.Button) -> float:
        return min(120, max(48, button.get_allocated_width() * 0.3))

    def _on_swipe_row_allocate(self, row, allocation, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.set_size_request(max(1, allocation.width), -1)
            self._move_swipe_row(key, self._swipe_offsets.get(key, 0))

    def _move_swipe_row(self, key: str, offset: float) -> None:
        row = self._swipe_rows.get(key)
        button = self._buttons.get(key)
        if row is not None and button is not None:
            self._swipe_offsets[key] = offset
            row.move(button, round(offset), 0)

    def _stop_close_swipe_animation(self, key: str) -> None:
        source = self._swipe_animations.pop(key, None)
        if source is not None:
            GLib.source_remove(source)

    def _animate_close_swipe(self, key: str, close: bool = False) -> None:
        self._stop_close_swipe_animation(key)
        button = self._buttons.get(key)
        if button is None:
            return
        start = self._swipe_offsets.get(key, 0)
        target = -button.get_allocated_width() if close else 0
        started = GLib.get_monotonic_time()

        def tick():
            if key not in self._buttons:
                self._swipe_animations.pop(key, None)
                return GLib.SOURCE_REMOVE
            progress = min(1, (GLib.get_monotonic_time() - started) / 180_000)
            self._move_swipe_row(key, start + (target - start) * (1 - (1 - progress) ** 3))
            if progress < 1:
                return GLib.SOURCE_CONTINUE
            self._swipe_animations.pop(key, None)
            if close:
                window = self._windows.get(key)
                if window is not None:
                    self._wayland.manage_window(window.window_id, "close")
                # Restore the row if the app keeps its window open (e.g. a
                # save confirmation or a rejected close request).
                self._animate_close_swipe(key)
            return GLib.SOURCE_REMOVE

        self._swipe_animations[key] = GLib.timeout_add(16, tick)

    def _on_close_swipe_update(
            self, gesture: Gtk.GestureDrag, dx: float, dy: float,
            key: str) -> None:
        button = self._buttons.get(key)
        if button is None:
            return
        if key not in self._swiping_windows:
            if dx > -10 or abs(dx) <= abs(dy):
                return
            self._swiping_windows.add(key)
            self._stop_close_swipe_animation(key)
            self._suppress_clicks.add(key)
            self._pending_long_presses.discard(key)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._long_press_gestures[key].set_state(Gtk.EventSequenceState.DENIED)
        self._move_swipe_row(key, min(0, dx))

    def _finish_close_swipe(self, key: str) -> bool:
        claimed = key in self._swiping_windows
        self._swiping_windows.discard(key)
        if claimed:
            # Keep release from activating the window before GTK finishes it.
            GLib.idle_add(self._clear_suppressed_click, key)
        return claimed

    def _on_close_swipe_end(
            self, _gesture: Gtk.GestureDrag, dx: float, dy: float,
            key: str) -> None:
        button = self._buttons.get(key)
        claimed = self._finish_close_swipe(key)
        if claimed and button is not None:
            self._animate_close_swipe(key, close=-dx >= self._close_swipe_distance(button))

    def _on_close_swipe_cancel(self, _gesture, _sequence, key: str) -> None:
        if self._finish_close_swipe(key):
            self._animate_close_swipe(key)

    def _on_clicked(self, _button, key: str) -> None:
        if key in self._swiping_windows:
            return
        if key in self._suppress_clicks:
            self._suppress_clicks.discard(key)
            return
        window = self._windows.get(key)
        if window is not None:
            self._wayland.manage_window(window.window_id, "activate")

    def _on_button_press(self, button, event, key: str) -> bool:
        if event.button != Gdk.BUTTON_SECONDARY:
            return False
        window = self._windows.get(key)
        if window is not None:
            self._show_window_menu(window, button, event)
        return True

    def _on_long_press(
            self, _gesture: Gtk.GestureLongPress, _x: float, _y: float,
            key: str) -> None:
        window = self._windows.get(key)
        button = self._buttons.get(key)
        if window is None or button is None or key in self._swiping_windows:
            return
        self._suppress_clicks.add(key)
        self._pending_long_presses.add(key)

    def _on_long_press_end(
            self, _gesture: Gtk.GestureLongPress, _sequence,
            key: str) -> None:
        if key in self._pending_long_presses:
            self._pending_long_presses.discard(key)
            # Open only after the release event has finished. Otherwise the
            # release which completes the long press also dismisses the menu.
            GLib.idle_add(self._open_long_press_menu, key)
        # Let GtkButton process the same release first; if it emits "clicked",
        # _on_clicked consumes the suppression before this idle cleanup runs.
        GLib.idle_add(self._clear_suppressed_click, key)

    def _open_long_press_menu(self, key: str) -> bool:
        window = self._windows.get(key)
        button = self._buttons.get(key)
        if window is not None and button is not None:
            self._show_window_menu(window, button)
        return GLib.SOURCE_REMOVE

    def _clear_suppressed_click(self, key: str) -> bool:
        self._suppress_clicks.discard(key)
        return GLib.SOURCE_REMOVE

    def _show_window_menu(
            self, window: WindowInfo, button: Gtk.Button,
            event=None) -> None:
        menu = Gtk.Menu()
        title = Gtk.MenuItem(label=window.title)
        title.set_sensitive(False)
        menu.append(title)
        menu.append(Gtk.SeparatorMenuItem())
        for label, action in (("Activate", "activate"), ("Minimize", "minimize"),
                              ("Restore", "restore"), ("Maximize", "maximize"),
                              ("Unmaximize", "unmaximize"), ("Close", "close")):
            item = Gtk.MenuItem(label=label)
            item.connect(
                "activate",
                lambda _item, window_id=window.window_id, selected_action=action:
                    self._wayland.manage_window(window_id, selected_action),
            )
            menu.append(item)
        menu.show_all()
        self._active_window_menu = menu
        menu.connect("deactivate", self._on_window_menu_closed)
        if event is not None:
            menu.popup_at_pointer(event)
        else:
            menu.popup_at_widget(
                button,
                Gdk.Gravity.SOUTH_WEST,
                Gdk.Gravity.NORTH_WEST,
                None,
            )

    def _on_window_menu_closed(self, _menu: Gtk.Menu) -> None:
        self._active_window_menu = None

    def _window_icon_image(self, window: WindowInfo):
        kind, value = self._desktop_icon_spec(window.app_id, window.title)
        if kind == "path":
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(value, self.ICON_SIZE, self.ICON_SIZE, True)
                return _fixed_icon_image(pixbuf, self.ICON_SIZE)
            except Exception:
                pass
        elif kind == "theme":
            try:
                pixbuf = Gtk.IconTheme.get_default().load_icon(value, self.ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
                return _fixed_icon_image(pixbuf, self.ICON_SIZE)
            except Exception:
                pass
        try:
            pixbuf = Gtk.IconTheme.get_default().load_icon(
                "application-x-executable",
                self.ICON_SIZE,
                Gtk.IconLookupFlags.FORCE_SIZE,
            )
            return _fixed_icon_image(pixbuf, self.ICON_SIZE)
        except Exception:
            image = Gtk.Image()
            image.set_size_request(self.ICON_SIZE, self.ICON_SIZE)
            return image


class Taskbar(Gtk.Window):
    HEIGHT = 28
    OSK_HEIGHT = 25

    def __init__(self):
        super().__init__(title="Taskbar")
        self._no_running_programs = True
        self._osk_visible = False
        self._launcher = ApplicationLauncher()
        self._launcher_control = LauncherControl(self._launcher)
        self.connect("destroy", lambda *_: self._launcher_control.close())
        self._running = RunningWindow(self._on_running_changed)

        self.set_decorated(False)
        self.connect("destroy", lambda *_: Gtk.main_quit())

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_exclusive_zone(self, self.HEIGHT)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(self, "wlroots-taskbar")

        compact_css = Gtk.CssProvider()
        compact_css.load_from_data(b"""
        window.taskbar-compact button.launch-button {
            font-size: 8px;
            min-height: 0;
            padding-top: 0;
            padding-bottom: 0;
        }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            compact_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._bar = bar
        for label, callback in (
                ("Launch", self._launcher.toggle),
                ("Running", self._running.toggle)):
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("launch-button")
            button.set_tooltip_text(label)
            button.connect("clicked", lambda _button, action=callback: action())
            bar.pack_start(button, True, True, 0)
        self.add(bar)

        self.set_size_request(-1, self.HEIGHT)
        self.resize(1, 1)
        self._keyboard_monitor = KeyboardVisibilityMonitor(self._set_osk_visible)
        GLib.idle_add(self._show_initial_launcher)

    def _set_osk_visible(self, visible: bool) -> None:
        if visible == self._osk_visible:
            return
        self._osk_visible = visible
        # Keep only the compact strip reserved while Squeekboard is visible,
        # placing its controls immediately below the keyboard.
        GtkLayerShell.set_exclusive_zone(
            self,
            self.OSK_HEIGHT if visible else self.HEIGHT,
        )
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        height = self.OSK_HEIGHT if visible else self.HEIGHT
        self.set_size_request(-1, height)
        self._bar.set_size_request(-1, height)
        style = self.get_style_context()
        if visible:
            style.add_class("taskbar-compact")
        else:
            style.remove_class("taskbar-compact")
        self.queue_resize()

    def _show_initial_launcher(self) -> bool:
        if self._no_running_programs:
            self._launcher.set_locked_open(True)
        return GLib.SOURCE_REMOVE

    def _on_running_changed(self, has_running: bool) -> None:
        had_no_running_programs = self._no_running_programs
        self._no_running_programs = not has_running
        self._launcher.set_locked_open(self._no_running_programs)
        if has_running and had_no_running_programs:
            self._launcher.hide()


def main() -> int:
    taskbar = Taskbar()
    statusbar = StatusBar()
    taskbar.show_all()
    statusbar.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
