"""Exercise brightness discovery and bounded hardware commands without GTK."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import system_backend as backend


class BrightnessTests(unittest.TestCase):
    def test_detects_valid_backlights_and_skips_unreadable_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / 'panel'
            device.mkdir()
            (device / 'max_brightness').write_text('200')
            (device / 'brightness').write_text('90')
            (Path(directory) / 'broken').mkdir()
            self.assertEqual(backend.backlights(directory), [{'name': 'panel', 'percent': 45}])

    def test_missing_backlight_directory_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(backend.backlights(str(Path(directory) / 'missing')), [])

    @patch.object(backend, 'run')
    def test_sets_only_selected_backlight(self, run):
        backend.set_brightness('intel_backlight', 65)
        run.assert_called_once_with('brightnessctl', '--class=backlight',
                                    '--device=intel_backlight', 'set', '65%', timeout=5)

    @patch.object(backend, 'run')
    def test_rejects_blackout_and_invalid_device(self, run):
        for device, percent in [('panel', 0), ('panel', 101), ('../panel', 50), ('-all', 50)]:
            with self.assertRaises(ValueError):
                backend.set_brightness(device, percent)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
