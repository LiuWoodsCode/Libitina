"""Check per-app rendering environments, D-Bus fallback, and debug details."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gi.repository import Gio, GLib
import app_launch
import settings


class SoftwareRenderingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="yui apps ")
        self.addCleanup(directory.cleanup)
        self.filename = Path(directory.name) / "org.example.App.desktop"
        self.filename.write_text('''[Desktop Entry]
Type=Application
Name=Example
Exec=/bin/true --file %k --literal %%k --url %U --name %c
Path=/tmp
Icon=example
DBusActivatable=true
''')
        config_patch = patch.object(settings, "CONFIG_PATH", Path(directory.name) / "config.json")
        config_patch.start()
        self.addCleanup(config_patch.stop)
        settings.set_software_rendering_apps([self.filename.name])
        self.app = Mock()
        self.app.get_id.return_value = self.filename.name
        self.app.get_filename.return_value = str(self.filename)
        self.app.get_display_name.return_value = "Example"
        self.app.get_boolean.side_effect = lambda key: key == "DBusActivatable"
        self.app.get_supported_types.return_value = ["text/plain"]
        self.app.get_string.return_value = None
        self.app.launch_uris_as_manager.return_value = True
        self.direct = Mock()
        self.direct.launch_uris_as_manager.return_value = True
        self.direct.launch.return_value = True
        self.factory = Mock(return_value=self.direct)
        factory_patch = patch.object(app_launch.Gio, "DesktopAppInfo",
                                     SimpleNamespace(new_from_keyfile=self.factory), create=True)
        factory_patch.start()
        self.addCleanup(factory_patch.stop)
        self.errors = Mock()

    def assert_software_environment(self, context):
        environment = dict(item.split("=", 1) for item in context.get_environment())
        self.assertEqual(environment["LIBGL_ALWAYS_SOFTWARE"], "1")
        self.assertEqual(environment["GSK_RENDERER"], "cairo")
        self.assertEqual(environment["QT_QUICK_BACKEND"], "software")

    def test_dbus_app_receives_environment_via_direct_exec(self):
        context = Gio.AppLaunchContext()
        original_environment = dict(os.environ)
        self.assertTrue(app_launch.launch_application(self.app, self.errors, context))
        self.app.launch_uris_as_manager.assert_not_called()
        self.assert_software_environment(context)
        self.assertEqual(dict(os.environ), original_environment)
        entry = self.factory.call_args.args[0]
        self.assertFalse(entry.get_boolean("Desktop Entry", "DBusActivatable"))
        self.assertEqual(entry.get_string("Desktop Entry", "Path"), "/tmp")
        self.assertEqual(entry.get_string("Desktop Entry", "Icon"), "example")
        command = entry.get_string("Desktop Entry", "Exec")
        self.assertIn(f'--file "{self.filename}"', command)
        self.assertIn("--literal %%k --url %U --name %c", command)
        self.errors.assert_not_called()

    def test_non_dbus_app_retains_original_entry(self):
        self.app.get_boolean.return_value = False
        self.app.get_boolean.side_effect = None
        self.assertTrue(app_launch.launch_application(self.app, self.errors))
        self.factory.assert_not_called()
        context = self.app.launch_uris_as_manager.call_args.args[1]
        self.assert_software_environment(context)

    def test_removal_takes_effect_without_restarting_shell(self):
        self.assertTrue(app_launch.launch_application(self.app, self.errors))
        settings.set_software_rendering_apps([])
        context = Gio.AppLaunchContext()
        before = context.get_environment()
        self.assertTrue(app_launch.launch_application(self.app, self.errors, context))
        self.assertEqual(context.get_environment(), before)
        self.app.launch_uris_as_manager.assert_called_once()
        self.assertEqual(self.factory.call_count, 1)

    def test_retry_uses_the_same_rendering_environment(self):
        context = Gio.AppLaunchContext()
        app_launch.launch_in_foot(self.app, context)
        self.assert_software_environment(context)
        entry = self.factory.call_args.args[0]
        self.assertTrue(entry.get_string("Desktop Entry", "Exec").startswith("foot --hold -- "))
        self.assertFalse(entry.get_boolean("Desktop Entry", "DBusActivatable"))
        self.direct.launch.assert_called_once_with([], context)

    def test_unusable_direct_entry_reports_launch_error(self):
        self.factory.return_value = None
        self.assertFalse(app_launch.launch_application(self.app, self.errors))
        self.errors.assert_called_once()
        self.assertIn("software rendering", self.errors.call_args.args[1])

    def test_direct_spawn_errors_are_reported(self):
        self.direct.launch_uris_as_manager.side_effect = GLib.Error("missing executable")
        self.assertFalse(app_launch.launch_application(self.app, self.errors))
        self.assertIn("missing executable", self.errors.call_args.args[1])

    def test_details_include_identity_source_and_effective_rendering(self):
        details = app_launch.application_details(self.app)
        self.assertIn(f"Desktop ID: {self.filename.name}", details)
        self.assertIn(f"Desktop file: {self.filename}", details)
        self.assertIn("Working directory: (not set)", details)
        self.assertIn("LIBGL_ALWAYS_SOFTWARE=1", details)
        self.assertIn(self.filename.read_text(), details)
        self.filename.unlink()
        self.assertIn("Could not read desktop file:", app_launch.application_details(self.app))


if __name__ == "__main__":
    unittest.main()
