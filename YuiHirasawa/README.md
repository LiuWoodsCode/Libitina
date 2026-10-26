# Yui display settings

Install Yui from a permanent checkout (the registered session points into this
folder). Run the installer as your normal login user; it uses `doas` or `sudo`
for system writes. Running as root is also supported, with `SUDO_USER` or
`DOAS_USER` selecting the user for optional autostart setup.

- Debian: `./install-debian.sh`
- postmarketOS: `./install-postmarketos.sh`
- Other Linux distributions, with dependencies installed manually:
  `./install-generic.sh`

All three register **YuiHirasawa** as an additional Wayland login session without
replacing Labwc's existing session. Add `--force-legacy` to also manage a launcher
block in the user's Labwc autostart. Repeated runs replace that block and preserve
other commands and file permissions. `XDG_CONFIG_HOME` is honored when absolute.

The generic installer installs no packages and requires Bash, `/usr/bin/python3`,
and standard Unix tools. Supply Labwc, Python bindings for GObject/GTK 3, Cairo,
D-Bus and dbus-next, PyWayland, GTK Layer Shell and libhandy typelibs, GStreamer
with base/good plugins, PAM, foot, wlr-randr, brightnessctl, NetworkManager, BlueZ,
UPower, PipeWire, and WirePlumber yourself. Install squeekboard to use the OSK.

The postmarketOS installer uses `apk add --no-cache` with the configured
repositories and includes squeekboard. Package availability depends on your
release and architecture; enable the appropriate official repositories before
running it. Alpine names PyWayland `py3-wayland`:
[Alpine package reference](https://pkgs.alpinelinux.org/package/v3.24/community/aarch64/py3-wayland).
It leaves service configuration to your existing postmarketOS setup. Ensure a
login manager supporting `/usr/share/wayland-sessions`, a session D-Bus,
`XDG_RUNTIME_DIR`, and the desired hardware/audio services are configured.
On systems without systemd-journald, Yui logs to its inherited stdout/stderr.

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

Set `"show_internal_watermark": true` in `~/.config/yui/config.json` to show
the OS pretty name, kernel version, Git branch and full commit hash, and
`I <3 Jamie Paige` at the bottom right of each desktop, above the bottom panel.
It defaults to false; changing the setting takes effect within a second.

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

Wallpaper is shared by desktop and mobile modes. In YuiSettings → Wallpaper,
choose Zohran, Shibuya, or a custom image file. Changes apply within one second;
images fill the screen with a centered crop. Disabling wallpaper uses black.
Unreadable or removed custom images fall back to Zohran.

Desktop mode starts the built-in background-layer utility automatically, with a
surface for each monitor (including hotplugged displays). It can also run alone
with `python3 wallpaper.py` in a Wayland session; avoid running it alongside the
desktop shell. The renderer uses GTK 3, gtk-layer-shell, and GdkPixbuf for scaling and centered
cropping; it does not require the Python Cairo foreign-struct converter.

The launcher routes its own logs and inherited stdout/stderr from the shell,
notification daemon, display worker, and session compositor to the systemd
journal using `systemd-cat`. Read or follow them with `journalctl -t yui -b`
or `journalctl -t yui -b -f`. Stdout uses info priority; stderr uses error priority.
Python children run unbuffered so output arrives promptly. The journal connection
is inherited across worker restarts. If `systemd-cat` or the journal socket is
unavailable, output stays on the original stdout/stderr. Restart the session to
apply this change. Programs that redirect their own output or use D-Bus activation
manage their logs separately.

The lock screen accepts your current Unix account password through PAM, including
letters, digits, punctuation, and Unicode. Type in the masked Password field and
press Enter or **Unlock**. Tap **On-screen keyboard** to request squeekboard via
its session D-Bus interface; the password field remains focused for typing.

To start and supervise squeekboard with the session, set
`"squeekboard_enabled": true` in `~/.config/yui/config.json` and restart the
session. This boolean defaults to `false`. Install the `squeekboard` package
first. The compositor must support layer-shell and virtual-keyboard input;
input-method support enables automatic keyboard display on focused text fields.
The keyboard uses its own reserved screen area alongside the lock screen.

The locker remains a layer-shell prototype, not a compositor-enforced session
lock; these changes do not add the Wayland session-lock protocol.

Lille theft protection is available in both shell layouts. Its dedicated Settings
section appears only when `enable_lille_config` is `true`. This controls visibility
of its settings, independently of whether protection itself is enabled. All Lille
options live in Settings → Lille. Configure `~/.config/yui/config.json`, for example:

```json
{
  "enable_lille_config": true,
  "enable_lille": true,
  "lille_location": "the reception desk",
  "lille_ssids": ["Store East", "Store West"],
  "lille_bssids": ["AA:BB:CC:DD:EE:FF"],
  "lille_require_charger": true,
  "lille_delay_seconds": 30,
  "lille_disable_screen": true,
  "lille_persist_trip": false,
  "lille_trip_value": false
}
```

Log out and back in after saving changes. All protection settings and identifiers
use the Lille name (`enable_lille` and `lille_location` replace the earlier keys).
SSID names are exact and case-sensitive; BSSID addresses are case-insensitive.
Enter one SSID or BSSID per line in Settings. A match with **any** configured entry
counts as nearby; the device does not have to connect to that access point.

With no SSIDs/BSSIDs, behavior stays charger-only: Lille arms if external power is
connected at shell startup, then trips when all chargers disconnect. The charger
factor toggle applies only when networks are configured. Charger status comes
from Linux `/sys/class/power_supply`, including fully charged batteries.

With network entries, Lille arms after observing a trusted access point. It trips
when **none** of those entries are visible, optionally also requiring the charger
to be disconnected. This works even if Yui started unplugged. A device that has
not yet seen a trusted access point does not trip merely because the first scan
finds none. The trusted observation is retained across worker restarts.

The condition must remain confirmed for `lille_delay_seconds` (0–86400). Seeing a
trusted AP, reconnecting required power, or losing reliable scan/power readings
resets the countdown. Wi-Fi scanning uses NetworkManager's `nmcli`, runs off the
GTK thread, and is attempted about every five seconds. Failed scans, disabled
Wi-Fi, and readings older than fifteen seconds count as unknown, not as absence.
Detection time includes the Wi-Fi scan interval; it is not a precise distance or
GPS geofence. See the [NetworkManager nmcli manual](https://networkmanager.dev/docs/api/latest/nmcli.html).

By default a trip hides Yui windows and notifications, stops its managed on-screen
keyboard, and displays the return message on every monitor. Turn off
`lille_disable_screen` to record a trip without blocking the screen. The runtime
trip and a journal message are still recorded. Reconnecting power or a trusted
network does not clear a completed trip.

Enable `lille_persist_trip` to save `lille_trip_value: true` to the configuration
when tripped. That restores the trip in new sessions and after reboot, even when
power or trusted networks are available. Without persistence a new session resets
the trip. Settings → Lille → Clear saved trip clears the configuration flag;
restart the Yui session to clear an active runtime latch. From another session or
terminal, set `lille_trip_value` to false (and optionally disable Lille) before
restarting. Hiding the Lille settings section does not disable protection.

The tracking/authorities text is a display message. Lille does not implement
tracking or contact authorities, and does not control layer surfaces owned by
unrelated applications. SSIDs/BSSIDs are proximity hints, not authenticated
location evidence.
