# Yui display settings

The desktop taskbar and mobile status bar refresh their network, Bluetooth,
volume, and battery icons every three seconds. Icons show Wi-Fi signal strength
or wired/disconnected state, audio level/mute, and battery level/charging.
Battery status uses UPower's combined system battery, excluding peripherals.

Click or tap any of these four buttons to open **Control Center**. It provides
volume and hardware brightness sliders, mute, Wi-Fi, and Bluetooth switches,
plus connection and battery status. Slider changes apply immediately after a
short pause. Close the window with **Close** or Escape. Unavailable controls are
disabled or marked unavailable, and failed changes display their error.
Brightness requires a supported Linux backlight and `brightnessctl`; external
monitor DDC/CI is not supported. Wi-Fi uses NetworkManager, audio uses
WirePlumber (`wpctl`), Bluetooth uses BlueZ, and battery status uses UPower.
The Debian installer includes these dependencies. Status reads and control
changes run off the UI thread, and each service can fail independently.

When `shell` is set to `mobile` in `~/.config/yui/config.json`, the YuiHirasawa
Labwc login session uses a temporary copy of the merged `rc.xml` settings with
a rule that opens all windows maximized and without server-side decorations.
The original configuration stays untouched. The copy is removed when Labwc
exits; changing shell mode takes effect at the next login.

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
Normal launches also run `Exec` directly when available so apps inherit Yui’s
current display and session environment instead of the D-Bus service environment.
Entries without an `Exec` command continue to use D-Bus activation.
The Debian installer includes foot; existing installations need the `foot` package.
The checks require PyGObject (`python3-gi` on Debian).

Right-click an application in either launcher to choose **Open** or **Details**.
Open behaves like a left click. Details opens a selectable debug view with the
desktop ID, desktop file location and contents, command, executable, working
directory, icon, startup WM class, categories, MIME types, terminal/D-Bus flags,
and whether Yui is configured to use software rendering for that app.

In YuiSettings, **Advanced → Software rendering** lets you add and remove exact
desktop IDs, including `.desktop`. Copy the ID from the launcher's Details view.
The list is saved as `software_rendering_apps` in `~/.config/yui/config.json`.
Both launchers read it on every launch, including retries in foot, without a
shell restart. Close any existing instance before relaunching the app.

Listed apps receive `LIBGL_ALWAYS_SOFTWARE=1`, `GSK_RENDERER=cairo`, and
`QT_QUICK_BACKEND=software` for Mesa OpenGL, GTK 4, and Qt Quick respectively.
Apps with D-Bus activation use their desktop file's `Exec` command directly so
the process receives these environment variables. Applications using other
rendering engines or ignoring these variables may need their own settings.
