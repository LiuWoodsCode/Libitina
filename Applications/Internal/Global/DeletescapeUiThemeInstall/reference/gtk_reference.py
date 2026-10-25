#!/usr/bin/env python3
"""GTK3 control specimen. Loads the bundled theme without installing it."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('light', 'dark'), default='dark')
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--dark-variant', action='store_true', help='Load gtk-dark.css from the light theme')
    parser.add_argument('--gallery', action='store_true', help='Show additional GTK widgets and states')
    args = parser.parse_args()
    import os
    import tempfile
    import shutil
    temporary = tempfile.TemporaryDirectory(prefix='dsui-reference-')
    theme_name = 'DeletescapeUi-Dark' if args.mode == 'dark' else 'DeletescapeUi'
    source = Path(__file__).resolve().parents[1] / 'themes'
    shutil.copytree(source, Path(temporary.name) / 'themes')
    # Configure GTK's theme search before importing it or caching XDG paths.
    os.environ['XDG_DATA_HOME'] = temporary.name
    os.environ['GTK_THEME'] = theme_name + (':dark' if args.dark_variant else '')
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
    settings = Gtk.Settings.get_default()
    settings.set_property('gtk-font-name', 'Arial 10')
    settings.set_property('gtk-enable-animations', False)
    settings.set_property('gtk-theme-name', theme_name)
    settings.set_property('gtk-application-prefer-dark-theme', args.dark_variant)
    # Explicitly parse the complete entry point, including all assets, as well.
    provider = Gtk.CssProvider()
    errors = []
    provider.connect('parsing-error', lambda _provider, section, error: errors.append(
        f'{section.get_start_line() + 1}: {error.message}'))
    filename = 'gtk-dark.css' if args.dark_variant else 'gtk.css'
    provider.load_from_path(str(source / theme_name / 'gtk-3.0' / filename))
    if errors:
        raise RuntimeError('\n'.join(errors))
    window = Gtk.OffscreenWindow() if args.screenshot else Gtk.Window(title=f'Deletescape UI · GTK · {args.mode}')
    window.set_default_size(600, 900)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    root.set_border_width(24)
    window.add(root)

    def label(text, style=None):
        widget = Gtk.Label(label=text, xalign=0)
        if style:
            widget.get_style_context().add_class(style)
        return widget

    title = label('Deletescape UI', 'title')
    title.set_margin_top(4)
    title.set_margin_bottom(4)
    root.pack_start(title, False, False, 0)
    root.pack_start(label('Widget reference / ' + args.mode, 'dim-label'), False, False, 0)
    root.pack_start(Gtk.Separator(), False, False, 0)

    def section(title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class('dsui-card')
        heading = label(title, 'dsui-section-title')
        box.pack_start(heading, False, False, 0)
        root.pack_start(box, False, False, 0)
        return box

    def row(box, widgets):
        line = Gtk.Box(spacing=12, homogeneous=True)
        for widget in widgets:
            line.pack_start(widget, True, True, 0)
        box.pack_start(line, False, False, 0)

    def button(text):
        widget = Gtk.Button(label=text)
        widget.get_child().set_xalign(0)
        return widget

    buttons = section('Actions')
    primary = button('Primary')
    primary.get_style_context().add_class('suggested-action')
    disabled = button('Disabled')
    disabled.set_sensitive(False)
    row(buttons, [button('Default'), primary, disabled])
    hover = button('Hover')
    hover.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
    pressed = button('Pressed')
    pressed.set_state_flags(Gtk.StateFlags.ACTIVE, False)
    keyboard = button('Keyboard focus')
    row(buttons, [hover, pressed, keyboard])
    fields = section('Fields')
    entry = Gtk.Entry(placeholder_text='Your name', width_chars=1)
    combo = Gtk.ComboBoxText()
    for item in ['Automatic', 'Light', 'Dark']:
        combo.append_text(item)
    combo.set_active(0)
    row(fields, [entry, combo])
    spin = Gtk.SpinButton.new_with_range(0, 100, 1)
    spin.set_value(42)
    spin.set_width_chars(1)
    unavailable = Gtk.Entry(placeholder_text='Unavailable', width_chars=1)
    unavailable.set_sensitive(False)
    row(fields, [spin, unavailable])
    text = Gtk.TextView()
    text.get_buffer().set_text('Write a note…')
    placeholder = text.get_buffer().create_tag('sample-placeholder', foreground='#808080')
    text.get_buffer().apply_tag(placeholder, *text.get_buffer().get_bounds())
    text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    scroll = Gtk.ScrolledWindow()
    scroll.get_style_context().add_class('frame')
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_size_request(-1, 68)
    scroll.add(text)
    fields.pack_start(scroll, False, False, 0)
    choices = section('Choices')
    check = Gtk.CheckButton(label='Checked')
    check.set_active(True)
    row(choices, [Gtk.CheckButton(label='Checkbox'), check])
    radio = Gtk.RadioButton.new_with_label_from_widget(None, 'Radio')
    radio_on = Gtk.RadioButton.new_with_label_from_widget(radio, 'Selected')
    radio_on.set_active(True)
    row(choices, [radio, radio_on])
    switches = []
    for active in (False, True):
        line = Gtk.Box(spacing=10)
        switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
        line.pack_start(switch, False, False, 0)
        line.pack_start(label('Switch on' if active else 'Switch off'), False, False, 0)
        switches.append(line)
    row(choices, switches)
    levels = section('Levels')
    slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    slider.set_draw_value(False)
    slider.set_value(65)
    slider.set_size_request(-1, 24)
    levels.pack_start(slider, False, False, 0)
    bar = Gtk.ProgressBar(fraction=0.65)
    levels.pack_start(bar, False, False, 0)
    root.pack_start(Gtk.Box(), True, True, 0)
    root.pack_start(label('Pink accent · flat surfaces · thin borders', 'dim-label'), False, False, 0)

    gallery = None
    if args.gallery:
        # Additional native widgets, selected and disabled states, and popovers.
        gallery = Gtk.OffscreenWindow() if args.screenshot else Gtk.Window(title='GTK integration gallery')
        gallery.set_default_size(540, 600)
        notebook = Gtk.Notebook()
        gallery.add(notebook)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, border_width=20)
        notebook.append_page(page, Gtk.Label(label='Navigation'))
        listing = Gtk.ListBox()
        for name in ['Network', 'Appearance', 'Notifications']:
            listing.add(Gtk.Label(label=name, xalign=0))
        listing.select_row(listing.get_row_at_index(1))
        page.pack_start(listing, False, False, 0)
        menu_button = Gtk.MenuButton(label='Open popover')
        popover = Gtk.Popover.new(menu_button)
        popover.add(Gtk.Label(label='Popover content', margin=16))
        popover.show_all()
        menu_button.set_popover(popover)
        page.pack_start(menu_button, False, False, 0)
        verticals = Gtk.Box(spacing=24)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 0, 100, 1)
        scale.set_size_request(-1, 120)
        verticals.add(scale)
        progress = Gtk.ProgressBar(orientation=Gtk.Orientation.VERTICAL, fraction=0.6)
        verticals.add(progress)
        page.add(verticals)
        page2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, border_width=20)
        notebook.append_page(page2, Gtk.Label(label='States'))
        for state, flag in [('Normal', Gtk.StateFlags.NORMAL), ('Hover', Gtk.StateFlags.PRELIGHT),
                            ('Pressed', Gtk.StateFlags.ACTIVE), ('Focus', Gtk.StateFlags.FOCUSED),
                            ('Disabled', Gtk.StateFlags.INSENSITIVE)]:
            line = Gtk.Box(spacing=12)
            line.pack_start(Gtk.Label(label=state, width_chars=10, xalign=0), False, False, 0)
            for widget in (Gtk.Button(label='Button'), Gtk.Entry(text='Entry', width_chars=6),
                           Gtk.CheckButton(active=True), Gtk.Switch(active=True, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)):
                widget.set_state_flags(flag, False)
                line.pack_start(widget, not isinstance(widget, (Gtk.Switch, Gtk.CheckButton)),
                                not isinstance(widget, (Gtk.Switch, Gtk.CheckButton)), 0)
            page2.add(line)
        gallery.connect('delete-event', lambda *_: gallery.hide() or True)
        gallery.show_all()

    window.connect('destroy', Gtk.main_quit)
    window.show_all()
    keyboard.grab_focus()

    def save(widget, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        pixbuf = widget.get_pixbuf()
        if pixbuf is None:
            raise RuntimeError('No GTK render available')
        pixbuf.savev(str(path), 'png', [], [])

    def capture():
        save(window, args.screenshot)
        if gallery:
            save(gallery, args.screenshot.with_stem(args.screenshot.stem + '-navigation'))
            notebook.set_current_page(1)
            GLib.timeout_add(100, capture_states)
        else:
            window.destroy()
        return False

    def capture_states():
        save(gallery, args.screenshot.with_stem(args.screenshot.stem + '-states'))
        gallery.destroy()
        window.destroy()
        return False

    if args.screenshot:
        GLib.timeout_add(250, capture)
    Gtk.main()
    temporary.cleanup()


if __name__ == '__main__':
    main()
