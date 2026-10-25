"""Small, bounded adapters for Linux services. No GTK or shell commands here."""

from __future__ import annotations

import getpass
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import uuid


class ServiceError(RuntimeError):
    pass


def run(*arguments: str, timeout: int = 20) -> str:
    try:
        result = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env={**os.environ, "LC_ALL": "C", "NO_COLOR": "1", "TERM": "dumb"},
            timeout=timeout, check=False,
        )
    except FileNotFoundError:
        raise ServiceError(f"{arguments[0]} is not installed.") from None
    except subprocess.TimeoutExpired:
        raise ServiceError(f"{arguments[0]} did not respond in time. Refresh to check its state.") from None
    except OSError as exc:
        raise ServiceError(str(exc)) from exc
    if result.returncode:
        raise ServiceError((result.stderr or result.stdout).strip() or
                           f"{arguments[0]} failed ({result.returncode}).")
    return result.stdout.strip()


def nm(*arguments: str) -> str:
    return run("nmcli", "--colors", "no", "--wait", "15", *arguments, timeout=25)


def split_terse(line: str) -> list[str]:
    """nmcli escapes both delimiters and literal backslashes in terse output."""
    fields, field, escaped = [], [], False
    for char in line:
        if escaped:
            field.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(field))
            field = []
        else:
            field.append(char)
    if escaped:
        field.append("\\")
    return [*fields, "".join(field)]


def nm_rows(fields: str, *arguments: str) -> list[list[str]]:
    output = nm("--terse", "--escape", "yes", "--mode", "tabular",
                "--fields", fields, *arguments)
    return [split_terse(line) for line in output.splitlines() if line]


def network_snapshot() -> dict:
    state = nm_rows("STATE,CONNECTIVITY,NETWORKING,WIFI-HW,WIFI,WWAN-HW,WWAN", "general", "status")
    devices = nm_rows("DEVICE,TYPE,STATE,CONNECTION", "device", "status")
    profiles = nm_rows("UUID,NAME,TYPE,DEVICE", "connection", "show")
    return {"state": state[0], "devices": devices, "profiles": profiles}


def wifi_snapshot(interface: str = "", scan: bool = False) -> dict:
    data = network_snapshot()
    adapters = [row for row in data["devices"] if row[1] == "wifi"]
    interface = interface if any(row[0] == interface for row in adapters) else (adapters[0][0] if adapters else "")
    data.update(adapters=adapters, interface=interface, access_points=[], saved_wifi={})
    for profile_uuid, _name, kind, _device in data["profiles"]:
        if kind not in ("802-11-wireless", "wifi"):
            continue
        try:
            settings = profile_settings(profile_uuid)
            ssid = bytes(settings.get("802-11-wireless", {}).get("ssid", [])).decode("utf-8")
            key_mgmt = settings.get("802-11-wireless-security", {}).get("key-mgmt", "open")
            data["saved_wifi"][(ssid, key_mgmt)] = profile_uuid
        except (ServiceError, ValueError, UnicodeError):
            continue
    if not interface or data["state"][4] != "enabled":
        return data
    data["access_points"] = nm_rows(
        "IN-USE,SSID,BSSID,SIGNAL,SECURITY,DBUS-PATH", "device", "wifi", "list",
        "ifname", interface, "--rescan", "yes" if scan else "no",
    )
    return data


def dbus_call(service, path, interface, method, signature=None, values=(), timeout=25000):
    # Import lazily so parsers and command adapters can be tested without GI.
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return bus.call_sync(
            service, path, interface, method,
            GLib.Variant(signature, values) if signature else None, None,
            Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout, None,
        ).unpack()
    except GLib.Error as exc:
        raise ServiceError(exc.message) from exc


NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"


def add_profile(name: str, kind: str, sections: dict) -> str:
    """Send credentials over D-Bus, never through process arguments or temp files."""
    from gi.repository import GLib
    profile_uuid = str(uuid.uuid4())
    settings = {
        "connection": {
            "id": GLib.Variant("s", name), "uuid": GLib.Variant("s", profile_uuid),
            "type": GLib.Variant("s", kind), "autoconnect": GLib.Variant("b", True),
            "permissions": GLib.Variant("as", [f"user:{getpass.getuser()}:"]),
        },
        "ipv4": {"method": GLib.Variant("s", "auto")},
        "ipv6": {"method": GLib.Variant("s", "auto")},
        **sections,
    }
    dbus_call(NM_SERVICE, NM_PATH + "/Settings", NM_SERVICE + ".Settings", "AddConnection",
              "(a{sa{sv}})", (settings,))
    return profile_uuid


def create_wifi(ssid: str, security: str, password: str, hidden: bool = False) -> str:
    from gi.repository import GLib
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("The network name must contain 1–32 UTF-8 bytes.")
    if security not in ("open", "wpa-psk", "sae", "owe"):
        raise ValueError("Use the connection editor for enterprise or legacy Wi-Fi security.")
    if security == "wpa-psk" and not (
        8 <= len(password.encode("utf-8")) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", password)
    ):
        raise ValueError("A WPA password needs 8–63 bytes, or 64 hexadecimal digits.")
    if security == "sae" and not password:
        raise ValueError("Enter the WPA3 password.")
    sections = {"802-11-wireless": {
        "ssid": GLib.Variant("ay", list(ssid.encode("utf-8"))),
        "mode": GLib.Variant("s", "infrastructure"), "hidden": GLib.Variant("b", hidden),
    }}
    if security != "open":
        sections["802-11-wireless-security"] = {"key-mgmt": GLib.Variant("s", security)}
        if security != "owe":
            sections["802-11-wireless-security"]["psk"] = GLib.Variant("s", password)
    return add_profile(ssid, "802-11-wireless", sections)


def activate_profile(profile_uuid: str, interface: str = ""):
    arguments = ["connection", "up", "uuid", profile_uuid]
    if interface:
        arguments += ["ifname", interface]
    return nm(*arguments)


def profile_settings(profile_uuid: str) -> dict:
    path = dbus_call(NM_SERVICE, NM_PATH + "/Settings", NM_SERVICE + ".Settings",
                     "GetConnectionByUuid", "(s)", (profile_uuid,))[0]
    return dbus_call(NM_SERVICE, path, NM_SERVICE + ".Settings.Connection", "GetSettings")[0]


def dns_servers(settings: dict, version: int) -> list[str]:
    if "dns-data" in settings:
        return settings["dns-data"]
    if version == 4:
        return [socket.inet_ntoa(struct.pack("=I", value)) for value in settings.get("dns", [])]
    return [str(ipaddress.IPv6Address(bytes(value))) for value in settings.get("dns", [])]


def save_network_preferences(profile_uuid: str, autoconnect: bool, metered: str, families: list[dict]):
    """Validate the whole edit before making one NetworkManager profile update."""
    if metered not in ("unknown", "yes", "no"):
        raise ValueError("Choose a valid metered preference.")
    if len(families) != 2:
        raise ValueError("Both IPv4 and IPv6 preferences are required.")
    arguments = ["connection", "modify", "uuid", profile_uuid,
                 "connection.autoconnect", "yes" if autoconnect else "no", "connection.metered", metered]
    for version, family in zip((4, 6), families):
        method = family["method"]
        if method in ("shared", "dhcp", "ignore"):
            continue  # Preserve advanced methods that this editor does not expose.
        if method not in ("auto", "manual", "disabled", "link-local"):
            raise ValueError(f"Unsupported IPv{version} method.")
        addresses = [value.strip() for value in family["addresses"].split(",") if value.strip()]
        dns = [value.strip() for value in family["dns"].split(",") if value.strip()]
        gateway = family["gateway"].strip()
        if method == "manual" and not addresses:
            raise ValueError(f"Enter at least one IPv{version} address with a prefix length.")
        for value in addresses:
            if "/" not in value or ipaddress.ip_interface(value).version != version:
                raise ValueError(f"Invalid IPv{version} address: {value}. Include its prefix length.")
        for value in [*dns, *([gateway] if gateway else [])]:
            if ipaddress.ip_address(value).version != version:
                raise ValueError(f"Invalid IPv{version} DNS server or gateway: {value}.")
        prefix = f"ipv{version}."
        arguments += [prefix + "method", method,
                      prefix + "addresses", ",".join(addresses) if method in ("auto", "manual") else "",
                      prefix + "gateway", gateway if method in ("auto", "manual") else "",
                      prefix + "dns", ",".join(dns) if method in ("auto", "manual") else "",
                      prefix + "ignore-auto-dns", "yes" if family["ignore_auto_dns"] else "no"]
    return nm(*arguments)


def create_cellular(name: str, apn: str, username: str, password: str, roaming: bool, device_id: str) -> str:
    from gi.repository import GLib
    if not name.strip() or not apn.strip() or not device_id:
        raise ValueError("Enter a connection name and APN, and select a modem.")
    gsm = {"apn": GLib.Variant("s", apn.strip()), "home-only": GLib.Variant("b", not roaming),
           "device-id": GLib.Variant("s", device_id)}
    for key, value in (("username", username), ("password", password)):
        if value:
            gsm[key] = GLib.Variant("s", value)
    return add_profile(name.strip(), "gsm", {"gsm": gsm})


def modem_snapshot() -> dict:
    data = network_snapshot()
    listed = json.loads(run("mmcli", "--list-modems", "--output-json"))
    modems = []
    for path in listed.get("modem-list", []):
        info = json.loads(run("mmcli", "--modem", path, "--output-json"))["modem"]
        modems.append((path, info))
    data["modems"] = modems
    return data


def unlock_sim(sim_path: str, pin: str):
    if not re.fullmatch(r"[0-9]{4,8}", pin):
        raise ValueError("A SIM PIN must contain 4–8 digits.")
    dbus_call("org.freedesktop.ModemManager1", sim_path,
              "org.freedesktop.ModemManager1.Sim", "SendPin", "(s)", (pin,))


def audio_snapshot() -> list[dict]:
    objects = json.loads(run("pw-dump"))
    nodes = []
    defaults = set()
    for target in ("@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"):
        try:
            match = re.search(r"\bid (\d+),", run("wpctl", "inspect", target))
            if match:
                defaults.add(match[1])
        except ServiceError:
            pass  # A system may have outputs but no microphone (or vice versa).
    # pw-dump's JSON format works with older WirePlumber releases too.
    for item in objects:
        props = item.get("info", {}).get("props", {})
        media_class = props.get("media.class")
        if media_class not in ("Audio/Sink", "Audio/Source"):
            continue
        node_id = str(item["id"])
        try:
            volume = run("wpctl", "get-volume", node_id)
        except ServiceError:
            continue  # A device may disappear between enumeration and inspection.
        match = re.search(r"Volume:\s+([0-9.]+)", volume)
        nodes.append({"id": node_id, "name": props.get("node.description") or props.get("node.nick") or props.get("node.name", node_id),
                      "kind": "Output" if media_class == "Audio/Sink" else "Input",
                      "volume": round(float(match[1]) * 100) if match else 0,
                      "muted": "[MUTED]" in volume, "default": node_id in defaults})
    return nodes


def clock_snapshot() -> dict:
    output = run("timedatectl", "show", "--property=Timezone,NTP,NTPSynchronized,CanNTP")
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def set_clock(method: str, signature: str, *values):
    return dbus_call("org.freedesktop.timedate1", "/org/freedesktop/timedate1",
                     "org.freedesktop.timedate1", method, signature, (*values, True))


def power_snapshot() -> dict:
    batteries, battery_error = [], ""
    try:
        paths = dbus_call("org.freedesktop.UPower", "/org/freedesktop/UPower",
                          "org.freedesktop.UPower", "EnumerateDevices")[0]
        for path in paths:
            device = dbus_call("org.freedesktop.UPower", path, "org.freedesktop.DBus.Properties",
                               "GetAll", "(s)", ("org.freedesktop.UPower.Device",))[0]
            if device.get("Type") == 2 and device.get("IsPresent"):
                batteries.append(device)
    except ServiceError as exc:
        battery_error = str(exc)
    # power-profiles-daemon is optional and independent of UPower.
    profiles, active, error = [], "", ""
    try:
        values = dbus_call("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles",
                           "org.freedesktop.DBus.Properties", "GetAll", "(s)",
                           ("net.hadess.PowerProfiles",))[0]
        profiles = [item["Profile"] for item in values.get("Profiles", [])]
        active = values.get("ActiveProfile", "")
    except ServiceError as exc:
        error = str(exc)
    return {"batteries": batteries, "battery_error": battery_error,
            "profiles": profiles, "active": active, "profile_error": error}


def set_power_profile(profile: str):
    from gi.repository import GLib
    dbus_call("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles",
              "org.freedesktop.DBus.Properties", "Set", "(ssv)",
              ("net.hadess.PowerProfiles", "ActiveProfile", GLib.Variant("s", profile)))
