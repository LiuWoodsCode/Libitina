"""Power transitions, session persistence, and configuration for Lille."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lille
import settings


class LilleProtectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state_path = self.root / 'state.json'

    def supply(self, name, kind, online=None, scope=None):
        path = self.root / name
        path.mkdir()
        (path / 'type').write_text(kind)
        if online is not None:
            (path / 'online').write_text(str(online))
        if scope is not None:
            (path / 'scope').write_text(scope)
        return path

    def state(self, connected, owner=True):
        return lille.LilleState(self.state_path, owner=owner, location='Front desk', connected=connected)

    def test_charger_online_is_independent_of_battery_charge(self):
        self.supply('BAT0', 'Battery', online=0)
        ac = self.supply('AC', 'Mains', online=1)
        self.assertIs(lille.charger_connected(self.root), True)
        (ac / 'online').write_text('0')
        self.assertIs(lille.charger_connected(self.root), False)

    def test_multiple_chargers_and_peripheral_power(self):
        self.supply('controller', 'USB', online=1, scope='Device')
        self.supply('AC', 'Mains', online=0)
        usb = self.supply('usb', 'USB', online=1, scope='System')
        self.assertIs(lille.charger_connected(self.root), True)
        (usb / 'online').write_text('0')
        self.assertIs(lille.charger_connected(self.root), False)

    def test_unknown_power_does_not_count_as_unplugged(self):
        self.assertIsNone(lille.charger_connected(self.root))
        ac = self.supply('AC', 'Mains', online='invalid')
        self.assertIsNone(lille.charger_connected(self.root))
        (ac / 'online').write_text('1')
        state = self.state(True)
        self.assertFalse(state.update(None))

    def test_only_starting_plugged_in_arms_protection(self):
        for initial in (False, None):
            with self.subTest(initial=initial):
                self.state_path.unlink(missing_ok=True)
                state = self.state(initial)
                self.assertFalse(state.update(True))
                self.assertFalse(state.update(False))

    def test_unplug_latches_across_replug_and_worker_restart(self):
        state = self.state(True)
        self.assertFalse(state.update(True))
        self.assertTrue(state.update(False))
        self.assertTrue(state.update(True))
        restarted = self.state(True)
        self.assertTrue(restarted.triggered)
        self.assertTrue(restarted.update(True))

    def test_armed_state_survives_shell_restart_before_unplug(self):
        self.state(True)
        restarted = self.state(False)
        self.assertTrue(restarted.update(False))

    def test_observer_does_not_arm_or_write_and_reads_owner_latch(self):
        observer = self.state(None, owner=False)
        self.assertFalse(self.state_path.exists())
        self.assertFalse(observer.update(False))
        owner = self.state(True)
        self.assertTrue(owner.update(False))
        self.assertTrue(observer.update(None))

    def test_session_tokens_isolate_latches(self):
        with patch.dict('os.environ', {'YUI_LILLE_SESSION': 'one', 'XDG_RUNTIME_DIR': str(self.root)}):
            first = lille.session_state_path()
        with patch.dict('os.environ', {'YUI_LILLE_SESSION': 'two', 'XDG_RUNTIME_DIR': str(self.root)}):
            second = lille.session_state_path()
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root)

    def test_disabled_does_not_read_power_or_create_state(self):
        with patch.object(lille, 'charger_connected') as power:
            controller = lille.LilleProtection(config={'enable_lille': False},
                                             state_path=self.state_path, reader=power)
            controller.close()
        power.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_configuration_defaults_round_trip_and_validation(self):
        with patch.object(settings, 'CONFIG_PATH', self.root / 'config.json'):
            config = settings.load_config()
            self.assertFalse(config['enable_lille'])
            config.update(enable_lille=True, lille_location='Library desk')
            settings.save_config(config)
            self.assertEqual(settings.load_config()['lille_location'], 'Library desk')
            saved = settings.CONFIG_PATH.read_bytes()
            for value in ('true', 1, None):
                config['enable_lille'] = value
                with self.assertRaises(ValueError):
                    settings.save_config(config)
                self.assertEqual(settings.CONFIG_PATH.read_bytes(), saved)
            config['enable_lille'] = True
            for value in ('', '   ', None, 123):
                config['lille_location'] = value
                with self.assertRaises(ValueError):
                    settings.save_config(config)
            settings.CONFIG_PATH.write_text(json.dumps({'enable_lille': 'true',
                                                         'lille_location': None}))
            loaded = settings.load_config()
            self.assertFalse(loaded['enable_lille'])
            self.assertEqual(loaded['lille_location'], 'the front desk')

    def test_supervisor_stops_managed_keyboard_without_restarting_it(self):
        import launcher
        from unittest.mock import Mock
        handlers = {}
        iterations = []
        def install(sig, handler):
            previous = handlers.get(sig)
            handlers[sig] = handler
            return previous
        def tick(_delay):
            iterations.append(1)
            if len(iterations) == 2:
                handlers[launcher.signal.SIGTERM](None, None)
        child = Mock()
        child.poll.return_value = None
        with patch.object(launcher.signal, 'signal', side_effect=install), \
                patch.object(launcher.time, 'sleep', side_effect=tick), \
                patch.object(launcher.subprocess, 'Popen', return_value=child) as spawn, \
                patch.object(lille, 'protection_triggered', side_effect=[False, True]):
            self.assertEqual(launcher.supervise({'squeekboard': ['squeekboard']}), 0)
        spawn.assert_called_once()
        child.terminate.assert_called()

    def network_state(self, **options):
        self.now = 0
        config = {**settings.DEFAULT_CONFIG, 'lille_ssids': ['Store'], **options}
        return lille.LilleState(self.state_path, owner=True, location='Desk', connected=False,
                                config=config, clock=lambda: self.now)

    def test_networks_arm_only_after_a_trusted_network_is_seen(self):
        state = self.network_state(lille_require_charger=False)
        self.assertFalse(state.update(False, False))
        self.assertFalse(state.update(False, None))
        self.assertFalse(state.update(False, True))
        self.assertTrue(state.update(False, False))

    def test_network_charger_option_and_legacy_behavior(self):
        state = self.network_state()
        state.update(True, True)
        self.assertFalse(state.update(True, False))
        self.assertFalse(state.update(None, False))
        self.assertTrue(state.update(False, False))
        self.state_path.unlink()
        state = self.network_state(lille_require_charger=False)
        state.update(True, True)
        self.assertTrue(state.update(True, False))
        self.state_path.unlink()
        state = self.network_state(lille_ssids=[], lille_require_charger=False)
        state.update(False, True)
        self.assertFalse(state.update(False, False))

    def test_delay_requires_continuous_confirmed_condition(self):
        state = self.network_state(lille_delay_seconds=10)
        state.update(False, True)
        self.assertFalse(state.update(False, False))
        self.now = 9
        self.assertFalse(state.update(False, False))
        state.update(False, True)  # Trusted network returns: cancel countdown.
        self.now = 20
        self.assertFalse(state.update(False, False))
        self.now = 29
        self.assertFalse(state.update(False, False))
        state.update(False, None)  # Unknown is not continued proof of absence.
        self.now = 40
        self.assertFalse(state.update(False, False))
        self.now = 50
        self.assertTrue(state.update(False, False))

    def test_charger_return_resets_delay_and_charger_only_supports_delay(self):
        state = self.network_state(lille_delay_seconds=5)
        state.update(False, True)
        state.update(False, False)
        self.now = 4
        state.update(True, False)
        self.now = 10
        self.assertFalse(state.update(False, False))
        self.now = 15
        self.assertTrue(state.update(False, False))
        self.state_path.unlink()
        state = lille.LilleState(self.state_path, owner=True, location='Desk', connected=True,
                                config={'lille_delay_seconds': 5}, clock=lambda: self.now)
        self.assertFalse(state.update(False))
        self.now = 20
        self.assertTrue(state.update(False))

    def test_any_matching_ssid_or_bssid_keeps_device_nearby(self):
        rows = [('Other', '00:11:22:33:44:55'), ('Store', 'AA:BB:CC:DD:EE:FF')]
        self.assertTrue(lille.trusted_nearby(rows, ['Store'], []))
        self.assertTrue(lille.trusted_nearby(rows, [], ['aa:bb:cc:dd:ee:ff']))
        self.assertFalse(lille.trusted_nearby(rows[:1], ['Store'], ['AA:BB:CC:DD:EE:FF']))
        self.assertIsNone(lille.trusted_nearby(None, ['Store'], []))

    def test_trip_persistence_and_new_boot(self):
        with patch.object(settings, 'CONFIG_PATH', self.root / 'config.json'):
            config = settings.load_config()
            config.update(enable_lille=True, lille_persist_trip=True)
            settings.save_config(config)
            state = lille.LilleState(self.state_path, owner=True, location='Desk', connected=True,
                                    config=config)
            self.assertTrue(state.update(False))
            self.assertTrue(settings.load_config()['lille_trip_value'])
            fresh = lille.LilleState(self.root / 'new-boot.json', owner=True, location='Desk',
                                    connected=True, config=settings.load_config())
            self.assertTrue(fresh.triggered)
            self.assertTrue(fresh.update(True))

    def test_nonpersistent_trip_and_record_only(self):
        with patch.object(settings, 'CONFIG_PATH', self.root / 'config.json'):
            config = settings.load_config()
            config.update(enable_lille=True, lille_disable_screen=False)
            settings.save_config(config)
            state = lille.LilleState(self.state_path, owner=True, location='Desk', connected=True,
                                    config=config)
            self.assertTrue(state.update(False))
            self.assertFalse(settings.load_config()['lille_trip_value'])
            with patch.object(lille, 'session_state_path', return_value=self.state_path):
                self.assertFalse(lille.protection_triggered())
            observer = lille.LilleState(self.state_path, owner=False, location='Desk', connected=None)
            self.assertTrue(observer.triggered)
            self.assertFalse(observer.disable_screen)
            fresh = lille.LilleState(self.root / 'fresh.json', owner=True, location='Desk',
                                    connected=True, config=settings.load_config())
            self.assertFalse(fresh.triggered)

    def test_configuration_validation(self):
        self.assertEqual(settings.validate_lille({'lille_bssids': ['aa:bb:cc:dd:ee:ff']})['lille_bssids'],
                         ['AA:BB:CC:DD:EE:FF'])
        for key, values in {
            'lille_ssids': [None, 'Store', [1], [''], ['x' * 33], ['a\nb']],
            'lille_bssids': [['bad'], ['11:22:33:44:55'], None],
            'lille_delay_seconds': [-1, True, '10', 86401],
            'enable_lille_config': ['true', 1],
            'lille_disable_screen': [1], 'lille_persist_trip': ['false'],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    settings.validate_lille({key: value})

    def test_wifi_scanner_escapes_and_errors(self):
        import subprocess
        from types import SimpleNamespace
        ok = lambda text: SimpleNamespace(returncode=0, stdout=text)
        output = r'Store\: East\\West:AA\:BB\:CC\:DD\:EE\:FF' + '\n'
        with patch.object(lille.subprocess, 'run', side_effect=[ok('enabled\n'), ok(output)]) as run:
            self.assertEqual(lille.scan_networks(), [('Store: East\\West', 'AA:BB:CC:DD:EE:FF')])
            self.assertIn('--rescan', run.call_args.args[0])
        with patch.object(lille.subprocess, 'run', side_effect=[ok('enabled\n'), ok('')]):
            self.assertEqual(lille.scan_networks(), [])
        for result in (ok('disabled\n'), SimpleNamespace(returncode=1)):
            with patch.object(lille.subprocess, 'run', return_value=result):
                self.assertIsNone(lille.scan_networks())

        for error in (FileNotFoundError(), subprocess.TimeoutExpired('nmcli', 15)):
            with patch.object(lille.subprocess, 'run', side_effect=error):
                self.assertIsNone(lille.scan_networks())

    def test_background_probe_does_not_block_and_discards_stale_results(self):
        import threading
        clock = [0]
        entered, release = threading.Event(), threading.Event()
        threads = []
        original_thread = threading.Thread
        def scan():
            entered.set()
            release.wait(1)
            return []
        def thread(*args, **kwargs):
            worker = original_thread(*args, **kwargs)
            threads.append(worker)
            return worker
        probe = lille.NetworkProbe(['Store'], [], scanner=scan, clock=lambda: clock[0])
        with patch.object(lille.threading, 'Thread', side_effect=thread):
            self.assertIsNone(probe.read())
            self.assertTrue(entered.wait(1))
            self.assertIsNone(probe.read())
            self.assertEqual(len(threads), 1)
            release.set()
            threads[0].join(1)
            self.assertFalse(threads[0].is_alive())
            self.assertIs(probe.read(), False)
            clock[0] = 16
            self.assertIsNone(probe.read())  # Old absence cannot keep a countdown running.
            threads[-1].join(1)
            self.assertFalse(threads[-1].is_alive())

    def test_invalid_loaded_options_fall_back_and_defaults_are_independent(self):
        with patch.object(settings, 'CONFIG_PATH', self.root / 'config.json'):
            config = settings.load_config()
            config['lille_ssids'].append('Store')
            self.assertEqual(settings.DEFAULT_CONFIG['lille_ssids'], [])
            settings.CONFIG_PATH.write_text(json.dumps({'lille_ssids': 'bad', 'lille_bssids': [123],
                'lille_delay_seconds': -5, 'lille_trip_value': 'true', 'enable_lille_config': 'true'}))
            config = settings.load_config()
            self.assertEqual(config['lille_ssids'], [])
            self.assertEqual(config['lille_bssids'], [])
            self.assertEqual(config['lille_delay_seconds'], 0)
            self.assertFalse(config['lille_trip_value'])
            self.assertFalse(config['enable_lille_config'])


if __name__ == '__main__':
    unittest.main()
