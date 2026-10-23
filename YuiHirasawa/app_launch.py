"""Launch desktop entries and report failures without blocking the shell."""

import time

from gi.repository import Gio, GLib


STARTUP_SECONDS = 10


def launch_in_foot(app, context=None) -> None:
    """Run the desktop entry's Exec command in a terminal that stays open."""
    entry = GLib.KeyFile()
    filename = app.get_filename()
    if filename:
        entry.load_from_file(filename, GLib.KeyFileFlags.NONE)
    else:
        entry.set_string("Desktop Entry", "Type", "Application")
        entry.set_string("Desktop Entry", "Name", app.get_display_name())
        entry.set_string("Desktop Entry", "Exec", app.get_commandline() or "")

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

    if context is None:
        context = Gio.AppLaunchContext()
    context.connect(
        "launch-failed",
        lambda *_: report("The application could not be activated."),
    )
    try:
        success = app.launch_uris_as_manager(
            [], context, GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
            None, None, spawned, None,
        )
    except (GLib.Error, OSError) as exc:
        report(str(exc))
        return False
    if not success:
        report("The application did not start.")
    return bool(success) and not reported
