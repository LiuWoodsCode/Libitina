#!/usr/bin/env python3
"""One-button, current-user GTK3 theme installer."""
from pathlib import Path
import os
import shutil
import tempfile

THEMES = ('DeletescapeUi', 'DeletescapeUi-Dark')


def theme_directory():
    configured = Path(os.environ.get('XDG_DATA_HOME', ''))
    base = configured if configured.is_absolute() else Path.home() / '.local' / 'share'
    return base / 'themes'


def install_themes():
    """Stage each complete theme before replacing an existing installation."""
    source = Path(__file__).resolve().parent / 'themes'
    destination = theme_directory()
    destination.mkdir(parents=True, exist_ok=True)
    for name in THEMES:
        with tempfile.TemporaryDirectory(prefix='.deletescape-', dir=destination) as temporary:
            staging = Path(temporary) / name
            backup = Path(temporary) / 'previous'
            shutil.copytree(source / name, staging)
            target = destination / name
            if target.exists() or target.is_symlink():
                target.replace(backup)
            try:
                staging.replace(target)
            except OSError:
                if backup.exists() or backup.is_symlink():
                    backup.replace(target)
                raise
    return destination


def main():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk

    app = Gtk.Application(application_id='io.github.pixelprowler.DeletescapeUiThemeInstall')

    def activate(application):
        if application.get_active_window():
            application.get_active_window().present()
            return
        window = Gtk.ApplicationWindow(application=application, title='Deletescape UI Themes')
        window.set_default_size(360, 240)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        layout.set_border_width(20)
        window.add(layout)
        title = Gtk.Label(label='Deletescape UI')
        title.get_style_context().add_class('title')
        layout.pack_start(title, False, False, 0)
        description = Gtk.Label(label='Install the light and dark GTK3 themes for your user.')
        description.set_line_wrap(True)
        layout.pack_start(description, False, False, 0)
        button = Gtk.Button(label='Install themes')
        button.get_style_context().add_class('suggested-action')
        layout.pack_start(button, False, False, 0)
        status = Gtk.Label(label=str(theme_directory()))
        status.set_line_wrap(True)
        status.set_selectable(True)
        layout.pack_start(status, True, True, 0)

        def install(_button):
            button.set_sensitive(False)
            try:
                destination = install_themes()
            except OSError as error:
                status.set_text(f'Installation failed: {error}\nYou can retry.')
            else:
                status.set_text(f'Installed both themes in {destination}.\n'
                                'Choose DeletescapeUi or DeletescapeUi-Dark in your appearance settings.')
                button.set_label('Reinstall themes')
            finally:
                button.set_sensitive(True)

        button.connect('clicked', install)
        window.show_all()

    app.connect('activate', activate)
    return app.run(None)


if __name__ == '__main__':
    raise SystemExit(main())
