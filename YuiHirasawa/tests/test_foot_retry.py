"""Check that terminal retries preserve desktop commands and report failures."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gi.repository import GLib

import app_launch


class FootRetryTests(unittest.TestCase):
    def setUp(self):
        config_patch = patch.object(app_launch, "load_config", return_value={"software_rendering_apps": []})
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.terminal = Mock()
        self.terminal.launch.return_value = True
        self.factory = Mock(return_value=self.terminal)
        desktop_info = SimpleNamespace(new_from_keyfile=self.factory)
        self.patch = patch.object(app_launch.Gio, "DesktopAppInfo", desktop_info,
                                  create=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_preserves_exec_arguments_and_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "example.desktop"
            filename.write_text('''[Desktop Entry]
Type=Application
Name=Example
Exec="/opt/My App/bin/example" --message "hello world" %U %% %i %c
Path=/tmp
Icon=example-icon
Terminal=true
DBusActivatable=true
TryExec=example
''', encoding="utf-8")
            app = Mock()
            app.get_filename.return_value = str(filename)
            context = object()
            app_launch.launch_in_foot(app, context)
            entry = self.factory.call_args.args[0]
            self.assertEqual(entry.get_string("Desktop Entry", "Exec"),
                             'foot --hold -- "/opt/My App/bin/example" '
                             '--message "hello world" %U %% %i %c')
            self.assertEqual(entry.get_string("Desktop Entry", "Path"), "/tmp")
            self.assertEqual(entry.get_string("Desktop Entry", "Icon"), "example-icon")
            self.assertFalse(entry.get_boolean("Desktop Entry", "Terminal"))
            self.assertFalse(entry.get_boolean("Desktop Entry", "DBusActivatable"))
            self.assertNotIn("TryExec", entry.get_keys("Desktop Entry")[0])
            self.terminal.launch.assert_called_once_with([], context)

    def app_without_file(self, command):
        app = Mock()
        app.get_filename.return_value = None
        app.get_commandline.return_value = command
        app.get_display_name.return_value = "Example"
        return app

    def test_entry_without_file(self):
        app_launch.launch_in_foot(self.app_without_file("example --flag"))
        entry = self.factory.call_args.args[0]
        self.assertEqual(entry.get_string("Desktop Entry", "Exec"),
                         "foot --hold -- example --flag")

    def test_missing_command_reports_error(self):
        with self.assertRaisesRegex(ValueError, "no command"):
            app_launch.launch_in_foot(self.app_without_file(None))
        self.factory.assert_not_called()

    def test_unusable_entry_reports_error(self):
        self.factory.return_value = None
        with self.assertRaisesRegex(ValueError, "Could not prepare"):
            app_launch.launch_in_foot(self.app_without_file("example"))

    def test_failed_terminal_launch_reports_error(self):
        self.terminal.launch.return_value = False
        with self.assertRaisesRegex(OSError, "foot is installed"):
            app_launch.launch_in_foot(self.app_without_file("example"))

    def test_terminal_launch_exception_is_propagated(self):
        self.terminal.launch.side_effect = GLib.Error("foot is missing")
        with self.assertRaisesRegex(GLib.Error, "foot is missing"):
            app_launch.launch_in_foot(self.app_without_file("example"))


if __name__ == "__main__":
    unittest.main()
