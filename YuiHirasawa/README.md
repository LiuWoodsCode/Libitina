# Yui display settings

Open **Display** in `Applications/Production/Yui/YuiSettings/main.py` to configure
monitors using `wlr-randr`. Controls include enabling outputs, resolution and
refresh rate, custom modes (for example `1920x1080@75Hz`), absolute X/Y position,
fractional scale, rotation/reflection, and adaptive sync where supported.
Enable **Custom mode** when entering a mode that the monitor does not advertise.
A blank resolution leaves mode selection to the compositor.

**Refresh displays** loads connected outputs and their saved settings.
**Use current display settings** reads the active layout instead; use **Save and
Apply** afterward to persist it. This also lets you replace a malformed saved
configuration. Settings for disconnected monitors are retained when saving.

The configuration lives in `~/.config/yui/monitors.json`, separate from the shell's
`config.json`. The launcher (`python3 YuiHirasawa/launcher.py`, also used by the
YuiHirasawa login session) supervises a display worker. It applies the saved
configuration at startup, after edits, and when the connected output names change.
Restart an already running Yui session once after upgrading to enable this worker.
Running `main.py` alone does not start the worker.

The worker checks for changes every two seconds, ignores disconnected outputs,
and leaves unconfigured outputs alone. It validates each configuration with
`wlr-randr --dryrun` before applying it and refuses to disable every connected
monitor. Apply results appear in Settings and in
`~/.config/yui/monitors-status.json`. A failed configuration stays saved for editing;
it is attempted again after an edit, a connection change, or a worker restart.

Requires a Wayland compositor with `wlr-output-management` support (such as Labwc)
and a `wlr-randr` version supporting `--json`. The Debian installer installs
`wlr-randr`; an older version without JSON support must be upgraded. Custom modes
and adaptive sync remain subject to compositor and hardware support.

Both desktop and mobile launchers show an error dialog when an application cannot
be started or activated, or when its directly launched process exits abnormally
within ten seconds. The dialog names the application and displays the error or
exit status. Normal exits do not show a dialog. Programs that detach into a
separate process or start through D-Bus cannot have their exit status monitored.
Choose **Try Again in foot** to rerun the application's command in foot and read
its output. Foot stays open after the program exits (`--hold`). This retry runs
the desktop entry's command directly, including for D-Bus activated applications.
The Debian installer includes foot; existing installations need the `foot` package.
The checks require PyGObject (`python3-gi` on Debian).
