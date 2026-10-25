"""Exercise persistence shared by the shell and standalone Settings payload."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings


class AppSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "yui" / "config.json"
        config_patch = patch.object(settings, "CONFIG_PATH", self.path)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def test_defaults_create_empty_list(self):
        config = settings.load_config()
        self.assertEqual(config["software_rendering_apps"], [])
        self.assertEqual(json.loads(self.path.read_text()), config)

    def test_existing_config_migrates_and_preserves_other_settings(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"wallpaper_enabled": False, "shell": "mobile", "future": 42}))
        self.assertEqual(settings.load_config()["software_rendering_apps"], [])
        settings.set_software_rendering_apps(["org.example.App.desktop", "org.example.App.desktop"])
        settings.set_wallpaper_enabled(True)
        settings.set_shell("desktop")
        config = json.loads(self.path.read_text())
        self.assertEqual(config["software_rendering_apps"], ["org.example.App.desktop"])
        self.assertEqual(config["future"], 42)
        self.assertTrue(config["wallpaper_enabled"])
        self.assertEqual(config["shell"], "desktop")
        settings.set_software_rendering_apps([])
        self.assertEqual(settings.load_config()["software_rendering_apps"], [])

    def test_wallpaper_selection_preserves_settings(self):
        settings.set_shell("mobile")
        settings.set_wallpaper_enabled(False)
        for value, filename in (("zohran", "zohran.png"), ("shibuya", "shibuya.jpg"),
                                ("/tmp/custom image.png", "custom image.png")):
            settings.set_wallpaper(value)
            config = settings.load_config()
            self.assertEqual(config["wallpaper"], value)
            self.assertEqual(settings.wallpaper_path(config).name, filename)
            self.assertFalse(config["wallpaper_enabled"])
            self.assertEqual(config["shell"], "mobile")

    def test_invalid_wallpaper_does_not_overwrite_config(self):
        settings.set_wallpaper("shibuya")
        saved = self.path.read_bytes()
        for value in (None, 42, "", "relative/image.png", []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.set_wallpaper(value)
            self.assertEqual(self.path.read_bytes(), saved)

    def test_old_and_malformed_wallpaper_configs_use_default(self):
        self.path.parent.mkdir()
        for data in ({}, {"wallpaper": []}, {"wallpaper": "relative.png"}):
            self.path.write_text(json.dumps(data))
            self.assertEqual(settings.load_config()["wallpaper"], "zohran")

    def test_invalid_ids_leave_saved_config_untouched(self):
        settings.set_software_rendering_apps(["valid.desktop"])
        saved = self.path.read_bytes()
        for invalid in ("valid.desktop", [None], [""], [".desktop"], ["App"],
                        ["/tmp/app.desktop"], ["two words.desktop"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                settings.set_software_rendering_apps(invalid)
            self.assertEqual(self.path.read_bytes(), saved)

    def test_malformed_list_does_not_prevent_shell_startup(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"shell": "mobile", "software_rendering_apps": 12}))
        config = settings.load_config()
        self.assertEqual(config["software_rendering_apps"], [])
        self.assertEqual(config["shell"], "mobile")

    def test_write_failure_keeps_previous_configuration(self):
        settings.set_software_rendering_apps(["valid.desktop"])
        saved = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("read only")):
            with self.assertRaises(OSError):
                settings.set_software_rendering_apps([])
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
