# Yui Settings

GTK 3 / libhandy settings for the Yui session. Launch with `./launch.sh` from
this directory, or install with `python3 Applications/install.py --environment
Yui` from the repository root. The installer copies all page modules and bundles
the shared Yui settings and display backends.

Display includes brightness sliders for available Linux hardware backlights.
Choose a percentage and click **Set brightness** to apply it immediately,
independently of the monitor layout's Apply button. Requires `brightnessctl`
(included by the Debian installer) and permission to control the backlight.
Unsupported displays show an unavailable message; external monitor DDC/CI
brightness is not supported.

Settings pages use native libhandy preference groups and action rows within a
centered, width-constrained layout. The app supplies no appearance CSS:
colors, borders, icons, and widget appearance come from the current GTK and icon themes.
Select a network, device, or monitor to open its options. An unsaved secured
Wi-Fi network prompts for its password; saved networks open their connection
editor. Saved-network management and device details live in dialogs. The
Advanced tab retains its original inline compatibility and software-rendering
controls. Audio volume, mute, device selection, and power-mode changes apply
directly. Monitor edits are staged until **Apply** is selected in the header.
In a monitor's options, enable **Custom resolution** to enter a mode such as
`1600x900@75Hz` directly. Disable it to return to the advertised-mode dropdown.

Available controls:

* **Wi-Fi:** adapter selection, radio power, scanning, open/WPA2/WPA3/OWE and
  hidden networks, reconnecting saved networks, disconnecting, and forgetting.
* **Cellular:** per-modem carrier/signal/lock status, mobile radio, SIM PIN,
  APN credentials, roaming preference, and saved data connections.
* **Networking:** Ethernet and saved VPN/other connections, current addresses
  and DNS, plus a native IPv4/IPv6, DNS, autoconnect, and metered-data editor.
  `nm-connection-editor` handles importing VPNs, enterprise Wi-Fi, proxy,
  credentials, and other advanced connection settings.
* **Bluetooth:** adapter selection, power, bounded discovery, pairing with PIN
  and passkey confirmation, connecting, disconnecting, trust, and forgetting.
* **Audio:** PipeWire input/output levels, mute, and default device selection.
* **Power & Battery:** UPower battery status/health, power profiles, and suspend.
* **Date & Time:** network time, time zone completion, and manual local time.
* **Text & Input:** GTK font, text scale, cursor blink/size, and animations.
* Existing wallpaper, appearance, display, advanced Yui, and system information
  pages remain available.

## Runtime services

The app expects a Linux system bus and a user session, with NetworkManager
(`nmcli`), ModemManager (`mmcli`, JSON output), BlueZ, PipeWire (`pw-dump`),
WirePlumber (`wpctl`), systemd-timedated (`timedatectl`), and systemd-logind.
UPower and power-profiles-daemon provide battery information and power modes
independently. `gsettings-desktop-schemas` supplies desktop text preferences.
An absent service or hardware device is reported on its page; other pages
remain usable. Refresh reads external changes; opening a service page also
refreshes its state. Discovery refreshes while scanning and stops on exit.

Run the app as your normal desktop user. Service policy controls authorization;
operations requiring authentication need a running graphical polkit agent in
the session. The app uses D-Bus interactive authorization for system changes
and displays service errors if permission is denied. It never invokes sudo.
Wi-Fi and APN credentials are sent directly to NetworkManager over D-Bus and
stored in a connection restricted to the current user. SIM PINs are sent to
ModemManager over D-Bus. Passwords/PINs are not placed in process arguments.

Network preference edits take effect on reconnection. Default audio devices
apply to new streams. GTK text preferences depend on application/desktop
support; these do not configure Labwc's libinput settings or keyboard layout.

## Checks

Run the service adapter regression tests without hardware or GI:

```sh
python3 -m unittest discover -s Applications/Production/Yui/YuiSettings/tests -v
```

With GTK, libhandy, and a graphical display, run the fixture-based UI checks:

```sh
python3 Applications/Production/Yui/YuiSettings/tests/gtk_smoke.py
```

This checks every page, row activation, password and connection dialogs,
staged monitor edits, direct audio controls, pairing confirmation, failure
recovery, and layouts down to 320 pixels. It uses an in-memory settings backend
and fixture services. Set `YUI_SETTINGS_SCREENSHOT_DIR` to capture desktop,
phone, and dialog layouts; `GTK_THEME=Adwaita:dark` checks the dark variant.

On Linux, also exercise real Wi-Fi authentication, SIM/APN activation, Bluetooth
pairing (including passkeys), audio hotplug, polkit challenges, and narrow-window
navigation with the target hardware. Unit tests use service fixtures and cannot
verify drivers, daemon policies, or radio behavior.
