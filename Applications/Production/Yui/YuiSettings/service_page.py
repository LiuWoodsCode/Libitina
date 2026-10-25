"""Theme-native preferences, dialogs, and bounded service-page workers."""

import threading

from gi.repository import Gio, GLib, GObject, Gtk, Handy


def label(text=""):
    widget = Gtk.Label(label=str(text), xalign=0, wrap=True)
    widget.set_max_width_chars(44)
    return widget


class PreferencesPage(Gtk.ScrolledWindow):
    """One scrolling surface with a comfortable desktop width and phone margins."""

    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_hexpand(True)
        self.set_vexpand(True)
        clamp = Handy.Clamp(maximum_size=660, tightening_threshold=440)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                            margin_start=12, margin_end=12, margin_top=24, margin_bottom=32)
        self.body.set_valign(Gtk.Align.START)
        clamp.add(self.body)
        self.add(clamp)


def group(box, title=""):
    widget = Handy.PreferencesGroup(title=title)
    box.pack_start(widget, False, False, 0)
    return widget


def action_row(container, title, subtitle="", icon=None, callback=None, suffix=None):
    widget = Handy.ActionRow(title=str(title), subtitle=str(subtitle),
                             title_lines=2, subtitle_lines=2)
    widget.set_selectable(False)
    if icon:
        widget.add_prefix(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR))
    if suffix is not None:
        suffix.set_valign(Gtk.Align.CENTER)
        widget.add(suffix)
    if callback:
        widget.set_activatable(True)
        widget.connect("activated", lambda *_: callback())
        if suffix is None:
            widget.add(Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.MENU))
    container.add(widget)
    return widget


def switch_row(container, title, active, callback=None, subtitle=""):
    switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
    row = action_row(container, title, subtitle, suffix=switch)
    row.set_activatable_widget(switch)
    if callback:
        switch.connect("notify::active", lambda widget, *_: callback(widget.get_active()))
    return switch


class Choice(GObject.Object):
    def __init__(self, value, title):
        super().__init__()
        self.value, self.title = str(value), title


class ChoiceRow(Handy.ComboRow):
    def __init__(self, container, title, items, active=None, changed=None):
        super().__init__(title=title, use_subtitle=True)
        self.set_title_lines(2)
        self.set_subtitle_lines(2)
        self._items = list(items)
        model = Gio.ListStore.new(Choice)
        for value, name in self._items:
            model.append(Choice(value, name))
        self.bind_name_model(model, lambda item, *_: item.title, None)
        self.set_selected_index(next((i for i, item in enumerate(self._items) if str(item[0]) == str(active)), 0))
        if changed:
            self.connect("notify::selected-index", lambda *_: changed(self.get_active_id()))
        container.add(self)

    def get_active_id(self):
        index = self.get_selected_index()
        return str(self._items[index][0]) if 0 <= index < len(self._items) else None


def choice_row(container, title, items, active=None, changed=None):
    return ChoiceRow(container, title, items, active, changed)


def field(container, title, widget):
    """Entries get the full row width; small controls stay beside their title."""
    if isinstance(widget, (Gtk.Entry, Gtk.ComboBoxText, Gtk.Scale, Gtk.Stack)) and not isinstance(widget, Gtk.SpinButton):
        row = Handy.PreferencesRow(activatable=False, selectable=False)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        caption = label(title)
        caption.get_style_context().add_class("dim-label")
        content.pack_start(caption, False, False, 0)
        if isinstance(widget, Gtk.Entry):
            widget.set_width_chars(12)
        content.pack_start(widget, False, False, 0)
        caption.set_mnemonic_widget(widget)
        row.add(content)
        container.add(row)
    else:
        row = action_row(container, title, suffix=widget)
        row.set_activatable_widget(widget)
    return widget


def combo(items, active=None):
    widget = Gtk.ComboBoxText()
    for value, title in items:
        widget.append(str(value), title)
    if active is not None:
        widget.set_active_id(str(active))
    elif items:
        widget.set_active(0)
    return widget


def icon_button(icon, tooltip, callback):
    widget = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    widget.set_relief(Gtk.ReliefStyle.NONE)
    widget.get_style_context().add_class("image-button")
    widget.set_tooltip_text(tooltip)
    widget.get_accessible().set_name(tooltip)
    widget.connect("clicked", lambda *_: callback())
    return widget


def make_refresh_button(tooltip, callback):
    return icon_button("view-refresh-symbolic", tooltip, callback)


def size_dialog(dialog, parent, width, height):
    parent = parent.get_toplevel()
    if isinstance(parent, Gtk.Window):
        available_width, available_height = parent.get_size()
        width, height = min(width, max(320, available_width)), min(height, max(240, available_height))
    dialog.set_default_size(width, height)


def present_dialog(parent, title, page, width=480, height=520):
    dialog = Gtk.Dialog(title=title, transient_for=parent.get_toplevel(), modal=True, use_header_bar=1)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    size_dialog(dialog, parent, width, height)
    dialog.get_content_area().pack_start(page, True, True, 0)
    dialog.connect("response", lambda widget, *_: widget.destroy())
    dialog.show_all()
    return dialog


def confirm(parent, title, detail):
    dialog = Gtk.MessageDialog(transient_for=parent.get_toplevel(), modal=True,
                               message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.OK_CANCEL, text=title)
    dialog.format_secondary_text(detail)
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.OK


def form(parent, title, fields):
    """A short preference dialog, with secrets cleared before destroying widgets."""
    dialog = Gtk.Dialog(title=title, transient_for=parent.get_toplevel(), modal=True, use_header_bar=1)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Continue", Gtk.ResponseType.OK)
    size_dialog(dialog, parent, 420, 340)
    page = PreferencesPage()
    content = group(page.body)
    dialog.get_content_area().pack_start(page, True, True, 0)
    for title, widget in fields:
        if isinstance(widget, Gtk.CheckButton):
            title = widget.get_label() or title
            widget.set_label("")
        field(content, title, widget)
        if isinstance(widget, Gtk.Entry):
            widget.set_activates_default(True)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.show_all()
    response = dialog.run()
    values = []
    for _, widget in fields:
        if isinstance(widget, Gtk.Entry):
            values.append(widget.get_text())
            if not widget.get_visibility():
                widget.set_text("")
        elif isinstance(widget, Gtk.ComboBoxText):
            values.append(widget.get_active_id())
        elif isinstance(widget, Gtk.CheckButton):
            values.append(widget.get_active())
    dialog.destroy()
    return values if response == Gtk.ResponseType.OK else None


class ServicePage(Gtk.Box):
    """Serialize service work; retain form edits after validation errors."""

    def __init__(self, refresh_title="Refresh"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._alive = True
        self._busy = False
        self.refresh_button = make_refresh_button(refresh_title, self.reload)
        page = PreferencesPage()
        self.status = label()
        self.status.set_selectable(True)
        self.status.set_no_show_all(True)
        self.status.connect("notify::label", lambda widget, *_: widget.set_visible(bool(widget.get_text())))
        page.body.pack_start(self.status, False, False, 0)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.body.pack_start(self.body, False, False, 0)
        self.pack_start(page, True, True, 0)
        self.connect("map", lambda *_: self.reload())
        self.connect("destroy", self._destroyed)

    def _destroyed(self, *_):
        self._alive = False

    def load(self):
        raise NotImplementedError

    def render(self, data):
        raise NotImplementedError

    def reload(self):
        self.perform()

    def perform(self, operation=None, message="", after=None):
        if self._busy or not self._alive:
            return
        self._busy = True
        self.body.set_sensitive(False)
        self.refresh_button.set_sensitive(False)
        self.status.set_text("Working…" if operation else "Reading settings…")

        def work():
            error, data, loaded = "", None, False
            try:
                if operation:
                    operation()
            except ValueError as exc:
                GLib.idle_add(done, None, False, str(exc), True)
                return
            except Exception as exc:
                error = str(exc)
            try:
                data = self.load()
                loaded = True
            except Exception as exc:
                error = "\n".join(part for part in (error, str(exc)) if part)
            GLib.idle_add(done, data, loaded, error)

        def done(data, loaded, error, invalid=False):
            if not self._alive:
                return False
            self._busy = False
            self.refresh_button.set_sensitive(True)
            if loaded:
                for child in self.body.get_children():
                    child.destroy()
                self.render(data)
                self.body.show_all()
            self.body.set_sensitive(loaded or invalid)
            self.status.set_text(error or (message if operation else ""))
            if not error and after:
                after()
            return False

        threading.Thread(target=work, daemon=True).start()
