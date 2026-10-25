"""Status presentation and controls with simulated Linux services."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_backend as backend


class StatusTests(unittest.TestCase):
    @patch.object(backend.services, 'run')
    def test_volume_levels_and_mute(self, run):
        for raw, level in [('0.00', 'muted'), ('0.20', 'low'), ('0.50', 'medium'), ('0.90', 'high'), ('0.90 [MUTED]', 'muted')]:
            run.return_value = 'Volume: ' + raw
            self.assertEqual(backend.audio_state()['icon'], f'audio-volume-{level}-symbolic')

    @patch.object(backend.services, 'nm', return_value='enabled')
    @patch.object(backend.services, 'nm_rows')
    def test_wifi_signal_levels(self, rows, nm):
        for signal, level in [(10, 'weak'), (40, 'ok'), (65, 'good'), (90, 'excellent')]:
            rows.side_effect = [[['wlan0', 'wifi', 'connected', 'Home']], [['*', str(signal)]]]
            state = backend.network_state()
            self.assertEqual(state['icon'], f'network-wireless-signal-{level}-symbolic')
            self.assertIn('Home', state['text'])
            self.assertTrue(state['wifi'])

    @patch.object(backend.services, 'nm', return_value='disabled')
    @patch.object(backend.services, 'nm_rows')
    def test_wired_and_disconnected(self, rows, nm):
        rows.return_value = [['eth0', 'ethernet', 'connected', 'Wired']]
        self.assertEqual(backend.network_state()['icon'], 'network-wired-symbolic')
        rows.return_value = [['wlan0', 'wifi', 'disconnected', '--']]
        state = backend.network_state()
        self.assertEqual(state['icon'], 'network-offline-symbolic')
        self.assertFalse(state['wifi'])

    @patch.object(backend.services, 'dbus_call')
    def test_battery_levels_charging_full_and_missing(self, call):
        for percent, state, icon in [(5, 2, 'empty'), (15, 2, 'caution'), (25, 2, 'low'), (60, 2, 'good'), (90, 1, 'full-charging'), (100, 4, 'full-charged')]:
            call.return_value = [{'IsPresent': True, 'Percentage': percent, 'State': state}]
            self.assertEqual(backend.battery_state()['icon'], f'battery-{icon}-symbolic')
        call.return_value = [{'IsPresent': False}]
        self.assertEqual(backend.battery_state()['icon'], 'battery-missing-symbolic')

    @patch.object(backend.services, 'dbus_call')
    def test_bluetooth_power_and_connected_devices(self, call):
        call.return_value = [{'/adapter': {'org.bluez.Adapter1': {'Powered': True}},
                             '/device': {'org.bluez.Device1': {'Connected': True}}}]
        state = backend.bluetooth_state()
        self.assertEqual(state['icon'], 'bluetooth-active-symbolic')
        self.assertEqual(state['adapters'], ['/adapter'])
        self.assertIn('1 connected', state['text'])

    def test_service_failure_does_not_hide_other_status(self):
        with patch.object(backend, 'network_state', side_effect=RuntimeError('No NetworkManager')), patch.object(backend, 'audio_state', return_value={'volume': 30}), patch.object(backend, 'battery_state', return_value={'text': 'Battery'}), patch.object(backend, 'bluetooth_state', return_value={'powered': False}), patch.object(backend.services, 'backlights', return_value=[]):
            state = backend.snapshot()
        self.assertEqual(state['network']['error'], 'No NetworkManager')
        self.assertEqual(state['audio']['volume'], 30)

    @patch.object(backend.services, 'run')
    @patch.object(backend.services, 'nm')
    def test_controls_target_default_output_and_wifi_radio(self, nm, run):
        backend.set_volume(200)
        run.assert_called_with('wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', '100%', timeout=5)
        backend.set_muted(True)
        run.assert_called_with('wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '1', timeout=5)
        backend.set_wifi(False)
        nm.assert_called_once_with('radio', 'wifi', 'off')


if __name__ == '__main__':
    unittest.main()
