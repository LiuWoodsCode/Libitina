#!/usr/bin/env python3
#
# Phosh GSettings Editor
#
# A mobile-friendly GTK3/PyGObject editor for installed GSettings schemas.
#
# WARNING:
#   This program can modify arbitrary GSettings keys. Some settings are
#   implementation details, unsupported, or capable of breaking applications
#   and desktop components. There is intentionally very little hand-holding.
#
# Dependencies, depending on distro:
#
#   Debian/Ubuntu/Mobian:
#       sudo apt install python3-gi gir1.2-gtk-3.0
#
#   Arch Linux:
#       sudo pacman -S python-gobject gtk3
#
#   Fedora:
#       sudo dnf install python3-gobject gtk3
#
# Run:
#       python3 phosh-gsettings-editor.py
#

import sys
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gdk, Gtk


APP_ID = "io.github.pixelprowler.PhoshGSettingsEditor"
APP_NAME = "GSettings Editor"


def escape_markup(text: str) -> str:
    return GLib.markup_escape_text(str(text))


def variant_to_text(value: Optional[GLib.Variant]) -> str:
    if value is None:
        return "(none)"

    try:
        return value.print_(True)
    except Exception:
        return str(value)


def truncate(text: str, length: int = 90) -> str:
    text = text.replace("\n", " ")
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"


def get_range_text(schema_key: Gio.SettingsSchemaKey) -> str:
    """
    Gio.SettingsSchemaKey.get_range() returns a variant describing the
    constraint. Its exact contents differ depending on whether the key is an
    enum, flags value, numeric range, etc.
    """
    try:
        value_range = schema_key.get_range()
        return variant_to_text(value_range)
    except Exception as exc:
        return f"(unavailable: {exc})"


def get_key_summary(schema_key: Gio.SettingsSchemaKey) -> str:
    try:
        summary = schema_key.get_summary()
        return summary or ""
    except Exception:
        return ""


def get_key_description(schema_key: Gio.SettingsSchemaKey) -> str:
    try:
        description = schema_key.get_description()
        return description or ""
    except Exception:
        return ""


class SchemaPathDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, schema_id: str):
        super().__init__(
            title="Relocatable Schema",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )

        self.set_default_size(420, -1)

        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("_Open", Gtk.ResponseType.OK)

        content = self.get_content_area()
        content.set_spacing(12)
        content.set_border_width(18)

        title = Gtk.Label()
        title.set_xalign(0)
        title.set_line_wrap(True)
        title.set_markup(
            "<b>This schema requires a path.</b>"
        )
        content.pack_start(title, False, False, 0)

        explanation = Gtk.Label()
        explanation.set_xalign(0)
        explanation.set_line_wrap(True)
        explanation.set_text(
            f"{schema_id} is a relocatable GSettings schema. "
            "Enter the object path whose settings you want to inspect."
        )
        content.pack_start(explanation, False, False, 0)

        example = Gtk.Label()
        example.set_xalign(0)
        example.set_line_wrap(True)
        example.set_text(
            "A GSettings path must begin and end with '/'.\n"
            "Example: /org/example/application/profile1/"
        )
        example.get_style_context().add_class("dim-label")
        content.pack_start(example, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("/org/example/object/")
        self.entry.set_activates_default(True)
        content.pack_start(self.entry, False, False, 0)

        self.error_label = Gtk.Label()
        self.error_label.set_xalign(0)
        self.error_label.set_line_wrap(True)
        self.error_label.get_style_context().add_class("error")
        content.pack_start(self.error_label, False, False, 0)

        self.set_default_response(Gtk.ResponseType.OK)

        self.show_all()

    def get_path(self) -> Optional[str]:
        while True:
            response = self.run()

            if response != Gtk.ResponseType.OK:
                self.destroy()
                return None

            path = self.entry.get_text().strip()

            if not path:
                self.error_label.set_text("Enter a path.")
                continue

            if not path.startswith("/"):
                self.error_label.set_text("The path must begin with '/'.")
                continue

            if not path.endswith("/"):
                self.error_label.set_text("The path must end with '/'.")
                continue

            if "//" in path:
                self.error_label.set_text(
                    "The path must not contain an empty component ('//')."
                )
                continue

            self.destroy()
            return path


class ValueEditorDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        settings: Gio.Settings,
        schema: Gio.SettingsSchema,
        schema_id: str,
        key_name: str,
        path: Optional[str],
    ):
        super().__init__(
            title=key_name,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )

        self.settings = settings
        self.schema = schema
        self.schema_id = schema_id
        self.key_name = key_name
        self.path = path

        self.schema_key = schema.get_key(key_name)

        self.set_default_size(560, 620)

        self.add_button("_Close", Gtk.ResponseType.CLOSE)

        content = self.get_content_area()
        content.set_spacing(0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        content.pack_start(scroller, True, True, 0)

        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
        )
        body.set_border_width(18)
        scroller.add(body)

        self._add_heading(body)

        summary = get_key_summary(self.schema_key)
        description = get_key_description(self.schema_key)

        if summary:
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_line_wrap(True)
            label.set_markup(f"<b>{escape_markup(summary)}</b>")
            body.pack_start(label, False, False, 0)

        if description:
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_line_wrap(True)
            label.set_selectable(True)
            label.set_text(description)
            body.pack_start(label, False, False, 0)

        info_grid = Gtk.Grid(
            column_spacing=14,
            row_spacing=8,
        )
        body.pack_start(info_grid, False, False, 4)

        value = settings.get_value(key_name)

        self.value_type = value.get_type()
        self.type_string = value.get_type_string()

        default_value = self.schema_key.get_default_value()

        self._info_row(
            info_grid,
            0,
            "Schema",
            schema_id,
        )

        self._info_row(
            info_grid,
            1,
            "Key",
            key_name,
        )

        self._info_row(
            info_grid,
            2,
            "Type",
            self.type_string,
        )

        if path:
            self._info_row(
                info_grid,
                3,
                "Path",
                path,
            )

        offset = 1 if path else 0

        self._info_row(
            info_grid,
            3 + offset,
            "Default",
            variant_to_text(default_value),
        )

        self._info_row(
            info_grid,
            4 + offset,
            "Range",
            get_range_text(self.schema_key),
        )

        separator = Gtk.Separator(
            orientation=Gtk.Orientation.HORIZONTAL
        )
        body.pack_start(separator, False, False, 4)

        edit_title = Gtk.Label()
        edit_title.set_xalign(0)
        edit_title.set_markup("<b>Value</b>")
        body.pack_start(edit_title, False, False, 0)

        syntax = Gtk.Label()
        syntax.set_xalign(0)
        syntax.set_line_wrap(True)
        syntax.set_text(
            "Values use GVariant syntax. For string keys, plain text is "
            "also accepted."
        )
        syntax.get_style_context().add_class("dim-label")
        body.pack_start(syntax, False, False, 0)

        self.value_view = Gtk.TextView()
        self.value_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.value_view.set_monospace(True)
        self.value_view.set_left_margin(8)
        self.value_view.set_right_margin(8)
        self.value_view.set_top_margin(8)
        self.value_view.set_bottom_margin(8)

        value_scroll = Gtk.ScrolledWindow()
        value_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        value_scroll.set_min_content_height(130)
        value_scroll.set_shadow_type(Gtk.ShadowType.IN)
        value_scroll.add(self.value_view)

        body.pack_start(value_scroll, False, False, 0)

        self.error_label = Gtk.Label()
        self.error_label.set_xalign(0)
        self.error_label.set_line_wrap(True)
        self.error_label.get_style_context().add_class("error")
        body.pack_start(self.error_label, False, False, 0)

        self.writable_label = Gtk.Label()
        self.writable_label.set_xalign(0)
        self.writable_label.set_line_wrap(True)
        body.pack_start(self.writable_label, False, False, 0)

        button_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        body.pack_start(button_box, False, False, 0)

        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.get_style_context().add_class(
            "suggested-action"
        )
        self.apply_button.connect("clicked", self.on_apply)
        button_box.pack_start(self.apply_button, True, True, 0)

        self.reset_button = Gtk.Button(label="Reset")
        self.reset_button.connect("clicked", self.on_reset)
        button_box.pack_start(self.reset_button, True, True, 0)

        reload_button = Gtk.Button(label="Reload")
        reload_button.connect("clicked", self.on_reload)
        button_box.pack_start(reload_button, True, True, 0)

        self.changed_handler = self.settings.connect(
            f"changed::{key_name}",
            self.on_external_change,
        )

        self.load_value()
        self.refresh_writable_state()

        self.show_all()

    def _add_heading(self, parent: Gtk.Box):
        label = Gtk.Label()
        label.set_xalign(0)
        label.set_selectable(True)
        label.set_line_wrap(True)
        label.set_markup(
            f"<span size='x-large'><b>"
            f"{escape_markup(self.key_name)}"
            f"</b></span>"
        )
        parent.pack_start(label, False, False, 0)

    def _info_row(
        self,
        grid: Gtk.Grid,
        row: int,
        title: str,
        value: str,
    ):
        title_label = Gtk.Label()
        title_label.set_xalign(1)
        title_label.set_yalign(0)
        title_label.set_markup(
            f"<b>{escape_markup(title)}</b>"
        )

        value_label = Gtk.Label()
        value_label.set_xalign(0)
        value_label.set_yalign(0)
        value_label.set_line_wrap(True)
        value_label.set_selectable(True)
        value_label.set_text(value)

        grid.attach(title_label, 0, row, 1, 1)
        grid.attach(value_label, 1, row, 1, 1)

    def get_editor_text(self) -> str:
        buffer = self.value_view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, True)

    def set_editor_text(self, text: str):
        self.value_view.get_buffer().set_text(text)

    def load_value(self):
        try:
            value = self.settings.get_value(self.key_name)

            # A plain string is much nicer to edit than:
            # 'hello, world'
            if value.get_type_string() == "s":
                self.set_editor_text(value.get_string())
            else:
                self.set_editor_text(variant_to_text(value))

            self.error_label.set_text("")

        except Exception as exc:
            self.error_label.set_text(
                f"Could not read value: {exc}"
            )

    def refresh_writable_state(self):
        try:
            writable = self.settings.is_writable(self.key_name)
        except Exception:
            writable = False

        self.value_view.set_editable(writable)
        self.apply_button.set_sensitive(writable)
        self.reset_button.set_sensitive(writable)

        if writable:
            self.writable_label.set_text("This key is writable.")
            self.writable_label.get_style_context().add_class(
                "dim-label"
            )
        else:
            self.writable_label.set_text(
                "This key is not writable. It may be locked by policy "
                "or by the active GSettings backend."
            )

    def parse_value(self, text: str) -> GLib.Variant:
        # For strings, accepting unquoted input makes editing much less
        # annoying. Users can still represent literally any string.
        if self.type_string == "s":
            return GLib.Variant("s", text)

        try:
            return GLib.Variant.parse(
                self.value_type,
                text,
                None,
                None,
            )
        except TypeError:
            # Compatibility with older PyGObject versions whose Variant.parse
            # binding takes a type string rather than a VariantType object.
            return GLib.Variant.parse(
                GLib.VariantType.new(self.type_string),
                text,
                None,
                None,
            )

    def on_apply(self, _button):
        text = self.get_editor_text()

        try:
            value = self.parse_value(text)
        except Exception as exc:
            self.error_label.set_text(
                f"Invalid {self.type_string} value:\n{exc}"
            )
            return

        try:
            # Gio.Settings.set_value() returns False if the value was rejected.
            result = self.settings.set_value(
                self.key_name,
                value,
            )

            if result is False:
                self.error_label.set_text(
                    "The GSettings backend rejected this value. "
                    "It may violate the schema's allowed range."
                )
                return

            Gio.Settings.sync()
            self.error_label.set_text("")
            self.load_value()

        except Exception as exc:
            self.error_label.set_text(
                f"Could not write value:\n{exc}"
            )

    def on_reset(self, _button):
        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Reset “{self.key_name}”?",
        )

        confirm.format_secondary_text(
            "The user value will be removed and the key will fall back "
            "to its default or system-provided value."
        )

        confirm.add_button(
            "_Cancel",
            Gtk.ResponseType.CANCEL,
        )
        confirm.add_button(
            "_Reset",
            Gtk.ResponseType.OK,
        )

        response = confirm.run()
        confirm.destroy()

        if response != Gtk.ResponseType.OK:
            return

        try:
            self.settings.reset(self.key_name)
            Gio.Settings.sync()
            self.error_label.set_text("")
            self.load_value()
        except Exception as exc:
            self.error_label.set_text(
                f"Could not reset value:\n{exc}"
            )

    def on_reload(self, _button):
        self.load_value()
        self.refresh_writable_state()

    def on_external_change(
        self,
        _settings,
        _key,
    ):
        # Do not forcibly replace text while the user is editing.
        self.set_title(f"{self.key_name} • changed")

    def do_destroy(self):
        try:
            self.settings.disconnect(self.changed_handler)
        except Exception:
            pass

        Gtk.Dialog.do_destroy(self)


class KeyRow(Gtk.ListBoxRow):
    def __init__(
        self,
        key_name: str,
        schema_key: Gio.SettingsSchemaKey,
        settings: Gio.Settings,
    ):
        super().__init__()

        self.key_name = key_name
        self.schema_key = schema_key
        self.settings = settings

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        box.set_border_width(12)

        name = Gtk.Label()
        name.set_xalign(0)
        name.set_markup(
            f"<b>{escape_markup(key_name)}</b>"
        )
        box.pack_start(name, False, False, 0)

        summary = get_key_summary(schema_key)
        if summary:
            summary_label = Gtk.Label()
            summary_label.set_xalign(0)
            summary_label.set_line_wrap(True)
            summary_label.set_max_width_chars(60)
            summary_label.set_text(summary)
            box.pack_start(
                summary_label,
                False,
                False,
                0,
            )

        try:
            value = settings.get_value(key_name)
            value_text = variant_to_text(value)
            type_string = value.get_type_string()
        except Exception as exc:
            value_text = f"(read error: {exc})"
            type_string = "?"

        details = Gtk.Label()
        details.set_xalign(0)
        details.set_line_wrap(True)
        details.set_selectable(True)
        details.get_style_context().add_class("dim-label")
        details.set_text(
            f"{type_string}  •  {truncate(value_text)}"
        )
        box.pack_start(details, False, False, 0)

        self.add(box)


class SchemaRow(Gtk.ListBoxRow):
    def __init__(
        self,
        schema_id: str,
        schema: Gio.SettingsSchema,
    ):
        super().__init__()

        self.schema_id = schema_id
        self.schema = schema

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        box.set_border_width(12)

        name = Gtk.Label()
        name.set_xalign(0)
        name.set_line_wrap(True)
        name.set_markup(
            f"<b>{escape_markup(schema_id)}</b>"
        )
        box.pack_start(name, False, False, 0)

        path = schema.get_path()

        detail = Gtk.Label()
        detail.set_xalign(0)
        detail.set_line_wrap(True)
        detail.get_style_context().add_class("dim-label")

        if path is None:
            detail.set_text("Relocatable schema")
        else:
            detail.set_text(path)

        box.pack_start(detail, False, False, 0)

        self.add(box)


class GSettingsEditorWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application)

        self.schema_source = Gio.SettingsSchemaSource.get_default()

        self.current_schema_id: Optional[str] = None
        self.current_schema: Optional[Gio.SettingsSchema] = None
        self.current_settings: Optional[Gio.Settings] = None
        self.current_path: Optional[str] = None

        self.schema_search_text = ""
        self.key_search_text = ""

        self.set_title(APP_NAME)
        self.set_default_size(460, 720)
        self.set_size_request(300, 400)

        self.build_headerbar()
        self.build_ui()

        self.load_schemas()

    def build_headerbar(self):
        self.header = Gtk.HeaderBar()
        self.header.set_show_close_button(True)
        self.header.set_title(APP_NAME)

        self.back_button = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic",
            Gtk.IconSize.BUTTON,
        )
        self.back_button.set_tooltip_text("Back")
        self.back_button.connect("clicked", self.on_back)
        self.back_button.set_no_show_all(True)
        self.header.pack_start(self.back_button)

        self.refresh_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic",
            Gtk.IconSize.BUTTON,
        )
        self.refresh_button.set_tooltip_text("Refresh")
        self.refresh_button.connect(
            "clicked",
            self.on_refresh,
        )
        self.header.pack_end(self.refresh_button)

        menu_button = Gtk.MenuButton()
        menu_button.set_image(
            Gtk.Image.new_from_icon_name(
                "open-menu-symbolic",
                Gtk.IconSize.BUTTON,
            )
        )

        menu = Gio.Menu()
        menu.append("About", "app.about")
        menu.append("Quit", "app.quit")
        menu_button.set_menu_model(menu)

        self.header.pack_end(menu_button)

        self.set_titlebar(self.header)

    def build_ui(self):
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT
        )
        self.stack.set_transition_duration(180)

        self.add(self.stack)

        self.schema_page = self.build_schema_page()
        self.key_page = self.build_key_page()

        self.stack.add_named(
            self.schema_page,
            "schemas",
        )
        self.stack.add_named(
            self.key_page,
            "keys",
        )

        self.stack.set_visible_child_name("schemas")

    def build_schema_page(self) -> Gtk.Widget:
        page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )

        search_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
        )
        search_box.set_border_width(8)

        self.schema_search = Gtk.SearchEntry()
        self.schema_search.set_placeholder_text(
            "Search schemas"
        )
        self.schema_search.connect(
            "search-changed",
            self.on_schema_search_changed,
        )
        search_box.pack_start(
            self.schema_search,
            False,
            False,
            0,
        )

        page.pack_start(search_box, False, False, 0)

        self.schema_list = Gtk.ListBox()
        self.schema_list.set_selection_mode(
            Gtk.SelectionMode.NONE
        )
        self.schema_list.set_activate_on_single_click(True)
        self.schema_list.connect(
            "row-activated",
            self.on_schema_activated,
        )
        self.schema_list.set_filter_func(
            self.filter_schema_row
        )

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        scroller.add(self.schema_list)

        page.pack_start(scroller, True, True, 0)

        return page

    def build_key_page(self) -> Gtk.Widget:
        page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )

        search_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
        )
        search_box.set_border_width(8)

        self.key_search = Gtk.SearchEntry()
        self.key_search.set_placeholder_text(
            "Search keys"
        )
        self.key_search.connect(
            "search-changed",
            self.on_key_search_changed,
        )
        search_box.pack_start(
            self.key_search,
            False,
            False,
            0,
        )

        page.pack_start(search_box, False, False, 0)

        self.key_list = Gtk.ListBox()
        self.key_list.set_selection_mode(
            Gtk.SelectionMode.NONE
        )
        self.key_list.set_activate_on_single_click(True)
        self.key_list.connect(
            "row-activated",
            self.on_key_activated,
        )
        self.key_list.set_filter_func(
            self.filter_key_row
        )

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )
        scroller.add(self.key_list)

        page.pack_start(scroller, True, True, 0)

        return page

    @staticmethod
    def clear_listbox(listbox: Gtk.ListBox):
        for child in listbox.get_children():
            listbox.remove(child)

    def load_schemas(self):
        self.clear_listbox(self.schema_list)

        if self.schema_source is None:
            self.show_error(
                "No GSettings schema source could be found."
            )
            return

        try:
            normal, relocatable = self.schema_source.list_schemas(
                True
            )
            schema_ids = sorted(
                set(normal) | set(relocatable),
                key=str.casefold,
            )
        except Exception as exc:
            self.show_error(
                f"Could not enumerate GSettings schemas:\n{exc}"
            )
            return

        for schema_id in schema_ids:
            schema = self.schema_source.lookup(
                schema_id,
                True,
            )

            if schema is None:
                continue

            self.schema_list.add(
                SchemaRow(schema_id, schema)
            )

        self.schema_list.show_all()
        self.schema_list.invalidate_filter()

    def open_schema(
        self,
        schema_id: str,
        schema: Gio.SettingsSchema,
    ):
        path = schema.get_path()

        try:
            if path is None:
                dialog = SchemaPathDialog(
                    self,
                    schema_id,
                )
                selected_path = dialog.get_path()

                if selected_path is None:
                    return

                settings = Gio.Settings.new_full(
                    schema,
                    None,
                    selected_path,
                )

                current_path = selected_path

            else:
                settings = Gio.Settings.new_full(
                    schema,
                    None,
                    None,
                )
                current_path = path

        except Exception as exc:
            self.show_error(
                f"Could not open schema {schema_id}:\n{exc}"
            )
            return

        self.current_schema_id = schema_id
        self.current_schema = schema
        self.current_settings = settings
        self.current_path = current_path

        self.load_keys()

        self.key_search.set_text("")
        self.stack.set_visible_child_name("keys")

        self.header.set_title(schema_id)

        if path is None:
            self.header.set_subtitle(current_path)
        else:
            self.header.set_subtitle(None)

        self.back_button.show()

    def load_keys(self):
        self.clear_listbox(self.key_list)

        if (
            self.current_schema is None
            or self.current_settings is None
        ):
            return

        try:
            keys = sorted(
                self.current_schema.list_keys(),
                key=str.casefold,
            )
        except Exception as exc:
            self.show_error(
                f"Could not enumerate schema keys:\n{exc}"
            )
            return

        for key_name in keys:
            try:
                schema_key = self.current_schema.get_key(
                    key_name
                )
            except Exception:
                continue

            row = KeyRow(
                key_name,
                schema_key,
                self.current_settings,
            )
            self.key_list.add(row)

        self.key_list.show_all()
        self.key_list.invalidate_filter()

    def on_schema_activated(
        self,
        _listbox,
        row,
    ):
        if not isinstance(row, SchemaRow):
            return

        self.open_schema(
            row.schema_id,
            row.schema,
        )

    def on_key_activated(
        self,
        _listbox,
        row,
    ):
        if not isinstance(row, KeyRow):
            return

        if (
            self.current_settings is None
            or self.current_schema is None
            or self.current_schema_id is None
        ):
            return

        dialog = ValueEditorDialog(
            parent=self,
            settings=self.current_settings,
            schema=self.current_schema,
            schema_id=self.current_schema_id,
            key_name=row.key_name,
            path=self.current_path,
        )

        dialog.run()
        dialog.destroy()

        # The displayed value may have changed.
        self.load_keys()

    def on_schema_search_changed(
        self,
        entry: Gtk.SearchEntry,
    ):
        self.schema_search_text = (
            entry.get_text().strip().casefold()
        )
        self.schema_list.invalidate_filter()

    def on_key_search_changed(
        self,
        entry: Gtk.SearchEntry,
    ):
        self.key_search_text = (
            entry.get_text().strip().casefold()
        )
        self.key_list.invalidate_filter()

    def filter_schema_row(
        self,
        row: Gtk.ListBoxRow,
    ) -> bool:
        if not self.schema_search_text:
            return True

        if not isinstance(row, SchemaRow):
            return True

        path = row.schema.get_path() or ""

        haystack = (
            row.schema_id + "\n" + path
        ).casefold()

        return self.schema_search_text in haystack

    def filter_key_row(
        self,
        row: Gtk.ListBoxRow,
    ) -> bool:
        if not self.key_search_text:
            return True

        if not isinstance(row, KeyRow):
            return True

        summary = get_key_summary(row.schema_key)
        description = get_key_description(row.schema_key)

        haystack = (
            row.key_name
            + "\n"
            + summary
            + "\n"
            + description
        ).casefold()

        return self.key_search_text in haystack

    def on_back(self, _button):
        self.stack.set_visible_child_name("schemas")

        self.current_schema_id = None
        self.current_schema = None
        self.current_settings = None
        self.current_path = None

        self.header.set_title(APP_NAME)
        self.header.set_subtitle(None)
        self.back_button.hide()

    def on_refresh(self, _button):
        page = self.stack.get_visible_child_name()

        if page == "schemas":
            self.load_schemas()
        else:
            self.load_keys()

    def show_error(self, message: str):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text="GSettings Editor",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()


class GSettingsEditorApplication(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

    def do_startup(self):
        Gtk.Application.do_startup(self)

        quit_action = Gio.SimpleAction.new(
            "quit",
            None,
        )
        quit_action.connect(
            "activate",
            lambda *_args: self.quit(),
        )
        self.add_action(quit_action)

        about_action = Gio.SimpleAction.new(
            "about",
            None,
        )
        about_action.connect(
            "activate",
            self.on_about,
        )
        self.add_action(about_action)

    def do_activate(self):
        window = self.props.active_window

        if window is None:
            window = GSettingsEditorWindow(self)

        window.show_all()

        # show_all() would otherwise reveal the initially hidden Back button.
        if window.stack.get_visible_child_name() == "schemas":
            window.back_button.hide()

        window.present()

    def on_about(self, *_args):
        parent = self.props.active_window

        dialog = Gtk.AboutDialog(
            transient_for=parent,
            modal=True,
        )

        dialog.set_program_name(APP_NAME)
        dialog.set_version("1.0")
        dialog.set_comments(
            "A compact, Phosh-friendly editor for installed "
            "GSettings schemas."
        )
        dialog.set_copyright(
            "Advanced configuration tool"
        )
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.set_website(
            "https://docs.gtk.org/gio/class.Settings.html"
        )
        dialog.set_website_label(
            "GSettings documentation"
        )

        dialog.run()
        dialog.destroy()


def install_css():
    css = b"""
    window {
        font-size: 11pt;
    }

    list row {
        border-bottom: 1px solid alpha(currentColor, 0.10);
    }

    list row:hover {
        background: alpha(currentColor, 0.06);
    }

    list row:active {
        background: alpha(currentColor, 0.12);
    }

    .dim-label {
        opacity: 0.70;
    }

    .error {
        color: #d7412a;
    }

    entry,
    textview {
        border-radius: 6px;
    }

    button {
        min-height: 34px;
    }

    headerbar button {
        min-width: 34px;
        min-height: 34px;
    }
    """

    provider = Gtk.CssProvider()

    try:
        provider.load_from_data(css)
    except GLib.Error:
        return

    screen = Gdk.Screen.get_default()

    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def main():
    install_css()

    app = GSettingsEditorApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())