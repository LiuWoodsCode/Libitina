"""Launch desktop entries and report failures without blocking the shell."""

from pathlib import Path
import re
import time

from gi.repository import Gio, GLib

from settings import load_config


STARTUP_SECONDS = 10
SOFTWARE_RENDERING_ENV = {
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "GSK_RENDERER": "cairo",
    "QT_QUICK_BACKEND": "software",
}


def software_rendering_enabled(app) -> bool:
    return app.get_id() in load_config()["software_rendering_apps"]


def _launch_context(app, context):
    context = context if context is not None else Gio.AppLaunchContext()
    software = software_rendering_enabled(app)
    if software:
        for variable, value in SOFTWARE_RENDERING_ENV.items():
            context.setenv(variable, value)
    return context, software


def _desktop_entry(app):
    """Copy an entry for direct execution, preserving its original %k path."""
    entry = GLib.KeyFile()
    filename = app.get_filename()
    if filename:
        entry.load_from_file(filename, GLib.KeyFileFlags.NONE)
    else:
        entry.set_string("Desktop Entry", "Type", "Application")
        entry.set_string("Desktop Entry", "Name", app.get_display_name())
        entry.set_string("Desktop Entry", "Exec", app.get_commandline() or "")

    command = entry.get_string("Desktop Entry", "Exec")
    if filename:
        quoted_filename = '"' + re.sub(r'([\\"`$])', r'\\\1', filename.replace("%", "%%")) + '"'
        command = re.sub(r"%%|%k", lambda match: quoted_filename if match[0] == "%k" else "%%", command)
        entry.set_string("Desktop Entry", "Exec", command)
    return entry


def launch_in_foot(app, context=None) -> None:
    """Run the desktop entry's Exec command in a terminal that stays open."""
    context, _software = _launch_context(app, context)
    entry = _desktop_entry(app)
    command = entry.get_string("Desktop Entry", "Exec")
    if not command.strip():
        raise ValueError("This application has no command to retry in foot.")
    # GIO expands the original Exec field codes and preserves Path and Icon.
    # Disable activation/terminal wrapping so output goes to this foot window.
    entry.set_string("Desktop Entry", "Exec", f"foot --hold -- {command}")
    entry.set_boolean("Desktop Entry", "Terminal", False)
    entry.set_boolean("Desktop Entry", "DBusActivatable", False)
    keys, _ = entry.get_keys("Desktop Entry")
    if "TryExec" in keys:
        entry.remove_key("Desktop Entry", "TryExec")
    terminal_app = Gio.DesktopAppInfo.new_from_keyfile(entry)
    if terminal_app is None:
        raise ValueError("Could not prepare this application for launch in foot.")
    if not terminal_app.launch([], context):
        raise OSError("Could not start foot. Check that foot is installed.")


def launch_application(app, on_error, context=None) -> bool:
    """Report spawn/activation errors and abnormal exits during startup.

    GIO retains desktop-entry handling (Exec field codes, working directory,
    terminal commands and D-Bus activation). Only directly spawned children can
    be watched; applications that detach or use D-Bus own their process lifecycle.
    """
    name = app.get_display_name()
    started = time.monotonic()
    reported = False

    def report(detail):
        nonlocal reported
        if not reported:
            reported = True
            on_error(f"Could not launch {name}", detail)

    def exited(pid, status):
        # Keep the watch even after startup so every child is reaped.
        GLib.spawn_close_pid(pid)
        if time.monotonic() - started > STARTUP_SECONDS:
            return
        try:
            GLib.spawn_check_wait_status(status)
        except GLib.Error as exc:
            report(f"The program exited during startup.\n\n{exc.message}")

    def spawned(_app, pid, _data):
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, exited)

    try:
        context, software = _launch_context(app, context)
        launch_app = app
        if software and app.get_boolean("DBusActivatable"):
            # D-Bus activation cannot carry a per-launch environment. Launch
            # the original Exec directly so this app receives the overrides.
            entry = _desktop_entry(app)
            entry.set_boolean("Desktop Entry", "DBusActivatable", False)
            launch_app = Gio.DesktopAppInfo.new_from_keyfile(entry)
            if launch_app is None:
                raise ValueError("Could not prepare this application for software rendering.")
        context.connect(
            "launch-failed",
            lambda *_: report("The application could not be activated."),
        )
        success = launch_app.launch_uris_as_manager(
            [], context, GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
            None, None, spawned, None,
        )
    except (GLib.Error, OSError, ValueError) as exc:
        report(str(exc))
        return False
    if not success:
        report("The application did not start.")
    return bool(success) and not reported


def application_details(app) -> str:
    """Return copyable launch metadata and the original desktop file."""
    icon = app.get_icon()
    software = software_rendering_enabled(app)
    values = (
        ("Name", app.get_display_name()),
        ("Desktop ID", app.get_id()),
        ("Desktop file", app.get_filename()),
        ("Description", app.get_description()),
        ("Executable", app.get_executable()),
        ("Command", app.get_commandline()),
        ("Working directory", app.get_string("Path")),
        ("Icon", icon.to_string() if icon else None),
        ("Startup WM class", app.get_startup_wm_class()),
        ("Categories", app.get_categories()),
        ("MIME types", ", ".join(app.get_supported_types() or [])),
        ("Terminal", app.get_boolean("Terminal")),
        ("D-Bus activation", app.get_boolean("DBusActivatable")),
        ("Software rendering", software),
    )
    details = "\n".join(
        f"{label}: {value if value is not None and value != '' else '(not set)'}"
        for label, value in values
    )
    if software:
        details += "\nLaunch environment:\n" + "\n".join(
            f"  {key}={value}" for key, value in SOFTWARE_RENDERING_ENV.items()
        )
        details += "\nYui launches Exec directly when software rendering is enabled."
    filename = app.get_filename()
    if filename:
        try:
            contents = Path(filename).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            contents = f"Could not read desktop file: {exc}"
        details += "\n\nDesktop file contents:\n\n" + contents
    return details
