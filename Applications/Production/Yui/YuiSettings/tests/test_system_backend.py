"""Service adapter tests: no running Linux services or graphical session required."""

import ipaddress
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import system_backend as backend


class Variant:
    def __init__(self, signature, value):
        self.signature, self.value = signature, value


class BackendTests(unittest.TestCase):
    def setUp(self):
        fake_gi = types.ModuleType("gi")
        repository = types.ModuleType("gi.repository")
        repository.GLib = types.SimpleNamespace(Variant=Variant)
        self.gi_patch = patch.dict(sys.modules, {"gi": fake_gi, "gi.repository": repository})
        self.gi_patch.start()
        self.addCleanup(self.gi_patch.stop)

    def test_terse_keeps_ipv6_mac_and_escaped_names(self):
        self.assertEqual(backend.split_terse(r"*:Cafe\: west\\east:AA\:BB\:CC:95:WPA2"),
                         ["*", "Cafe: west\\east", "AA:BB:CC", "95", "WPA2"])
        self.assertEqual(backend.split_terse(":name::"), ["", "name", "", ""])

    @patch.object(backend.subprocess, "run")
    def test_commands_are_bounded_noninteractive_and_not_shell_interpreted(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok\n", "")
        self.assertEqual(backend.run("nmcli", "a;$(touch /tmp/nope)"), "ok")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ("nmcli", "a;$(touch /tmp/nope)"))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(kwargs["timeout"], 20)
        self.assertNotIn("shell", kwargs)

    @patch.object(backend.subprocess, "run")
    def test_command_failures_are_actionable(self, run):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("nmcli", 20)):
            run.side_effect = failure
            with self.assertRaises(backend.ServiceError):
                backend.run("nmcli")
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 1, "", "Not authorized")
        with self.assertRaisesRegex(backend.ServiceError, "Not authorized"):
            backend.run("nmcli")

    @patch.object(backend, "dbus_call")
    @patch.object(backend, "run")
    def test_wifi_secrets_are_only_sent_over_dbus(self, run, call):
        uid = backend.create_wifi("Café: home", "wpa-psk", "secret123")
        run.assert_not_called()
        settings = call.call_args.args[5][0]
        self.assertEqual(bytes(settings["802-11-wireless"]["ssid"].value).decode(), "Café: home")
        self.assertEqual(settings["802-11-wireless-security"]["psk"].value, "secret123")
        self.assertEqual(settings["connection"]["uuid"].value, uid)
        self.assertTrue(settings["connection"]["permissions"].value[0].startswith("user:"))

    @patch.object(backend, "dbus_call")
    def test_invalid_wifi_never_creates_profile(self, call):
        for ssid, security, password in (("", "open", ""), ("é" * 17, "open", ""),
                                         ("home", "wpa-psk", "short"), ("home", "sae", ""),
                                         ("home", "wpa-eap", "secret123")):
            with self.subTest(ssid=ssid, security=security), self.assertRaises(ValueError):
                backend.create_wifi(ssid, security, password)
        call.assert_not_called()

    @patch.object(backend, "dbus_call")
    def test_open_and_hidden_wifi(self, call):
        backend.create_wifi("hidden", "open", "", hidden=True)
        settings = call.call_args.args[5][0]
        self.assertNotIn("802-11-wireless-security", settings)
        self.assertTrue(settings["802-11-wireless"]["hidden"].value)

    @patch.object(backend, "dbus_call")
    def test_apn_targets_selected_modem_and_disallows_roaming_by_default(self, call):
        backend.create_cellular("Mobile data", "internet", "alice", "secret", False, "modem-one")
        gsm = call.call_args.args[5][0]["gsm"]
        self.assertEqual(gsm["device-id"].value, "modem-one")
        self.assertTrue(gsm["home-only"].value)
        self.assertEqual(gsm["password"].value, "secret")

    @patch.object(backend, "dbus_call")
    def test_invalid_sim_pin_never_consumes_an_attempt(self, call):
        for pin in ("1", "123456789", "abcd", "１２３４"):
            with self.assertRaises(ValueError):
                backend.unlock_sim("/sim/0", pin)
        call.assert_not_called()
        backend.unlock_sim("/sim/0", "1234")
        self.assertEqual(call.call_args.args[3:], ("SendPin", "(s)", ("1234",)))

    def test_dns_supports_new_and_legacy_networkmanager(self):
        self.assertEqual(backend.dns_servers({"dns-data": ["1.1.1.1"]}, 4), ["1.1.1.1"])
        number = struct.unpack("=I", socket.inet_aton("1.2.3.4"))[0]
        self.assertEqual(backend.dns_servers({"dns": [number]}, 4), ["1.2.3.4"])
        self.assertEqual(backend.dns_servers({"dns": [list(ipaddress.IPv6Address("2001:db8::1").packed)]}, 6), ["2001:db8::1"])

    @staticmethod
    def families():
        return [{"method": "manual", "addresses": "192.168.1.10/24", "gateway": "192.168.1.1",
                 "dns": "1.1.1.1", "ignore_auto_dns": True},
                {"method": "auto", "addresses": "", "gateway": "", "dns": "", "ignore_auto_dns": False}]

    @patch.object(backend, "nm")
    def test_invalid_ip_edit_is_atomic(self, nm):
        families = self.families()
        families[1]["dns"] = "1.1.1.1"
        with self.assertRaises(ValueError):
            backend.save_network_preferences("uuid", True, "yes", families)
        nm.assert_not_called()
        families = self.families()
        families[0]["addresses"] = "192.168.1.10"
        with self.assertRaises(ValueError):
            backend.save_network_preferences("uuid", True, "yes", families)
        nm.assert_not_called()

    @patch.object(backend, "nm")
    def test_valid_ip_edit_is_one_update(self, nm):
        backend.save_network_preferences("uuid", False, "yes", self.families())
        self.assertEqual(nm.call_count, 1)
        arguments = nm.call_args.args
        self.assertEqual(arguments[:4], ("connection", "modify", "uuid", "uuid"))
        self.assertIn("192.168.1.10/24", arguments)
        self.assertIn("ipv6.method", arguments)

    @patch.object(backend, "dbus_call")
    def test_power_profiles_survive_unavailable_upower(self, call):
        call.side_effect = [backend.ServiceError("UPower absent"),
                            ({"Profiles": [{"Profile": "balanced"}], "ActiveProfile": "balanced"},)]
        data = backend.power_snapshot()
        self.assertEqual(data["profiles"], ["balanced"])
        self.assertEqual(data["battery_error"], "UPower absent")

    @patch.object(backend, "run")
    def test_audio_handles_unplug_and_missing_microphone(self, run):
        run.side_effect = [json.dumps([
            {"id": 42, "info": {"props": {"media.class": "Audio/Sink", "node.description": "Speaker"}}},
            {"id": 43, "info": {"props": {"media.class": "Audio/Source"}}},
            {"id": 44, "info": {"props": {"media.class": "Video/Source"}}},
        ]), "id 42, type PipeWire:Interface:Node", backend.ServiceError("No microphone"),
            "Volume: 0.53 [MUTED]", backend.ServiceError("Unplugged")]
        nodes = backend.audio_snapshot()
        self.assertEqual(len(nodes), 1)
        self.assertTrue(nodes[0]["default"])
        self.assertEqual(nodes[0]["volume"], 53)
        self.assertTrue(nodes[0]["muted"])

    @patch.object(backend, "nm_rows")
    def test_no_wifi_hardware_does_not_scan(self, rows):
        rows.side_effect = [[["disconnected", "none", "enabled", "missing", "disabled", "missing", "disabled"]], [], []]
        data = backend.wifi_snapshot(scan=True)
        self.assertEqual(data["interface"], "")
        self.assertEqual(data["access_points"], [])
        self.assertEqual(rows.call_count, 3)


if __name__ == "__main__":
    unittest.main()
