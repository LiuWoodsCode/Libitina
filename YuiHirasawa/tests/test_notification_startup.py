"""A notification daemon without GTK must not take over the D-Bus service."""

import ast
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def startup(initialized, display):
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'notification.py').read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'display_ready', 'main'}]
    namespace = {
        'GTK_DISPLAY_INITIALIZED': initialized,
        'Gdk': SimpleNamespace(Display=SimpleNamespace(get_default=Mock(return_value=display))),
        'LOGGER': Mock(), 'logging': logging, 'os': os, 'dbus': Mock(),
        'NotificationCenter': Mock(), 'NotificationService': Mock(),
        'GTK_LAYER_SHELL_AVAILABLE': True, 'Gtk': Mock(),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'notification.py', 'exec'), namespace)
    return namespace


class NotificationStartupTests(unittest.TestCase):
    def test_failed_import_time_initialization_exits_before_dbus(self):
        # Even a display opened later cannot undo PyGObject's cached failure.
        for display in (None, object()):
            with self.subTest(display=display):
                ns = startup(False, display)
                self.assertEqual(ns['main'](), 1)
                ns['dbus'].SessionBus.assert_not_called()
                ns['dbus'].service.BusName.assert_not_called()
                ns['NotificationCenter'].assert_not_called()
                ns['Gtk'].main.assert_not_called()
                ns['LOGGER'].error.assert_called_once()

    def test_lost_display_exits_before_dbus(self):
        ns = startup(True, None)
        self.assertEqual(ns['main'](), 1)
        ns['dbus'].SessionBus.assert_not_called()

    def test_valid_display_starts_service(self):
        ns = startup(True, object())
        self.assertEqual(ns['main'](), 0)
        ns['dbus'].service.BusName.assert_called_once()
        ns['NotificationCenter'].assert_called_once()
        ns['Gtk'].main.assert_called_once()

    def test_logs_relevant_session_environment(self):
        ns = startup(False, None)
        environment = {'WAYLAND_DISPLAY': 'wayland-1', 'XDG_RUNTIME_DIR': '/run/user/1000'}
        with patch.dict(os.environ, environment, clear=True):
            self.assertFalse(ns['display_ready']())
        args = ns['LOGGER'].error.call_args.args
        self.assertEqual(args[1:], ('wayland-1', None, '/run/user/1000', None))


if __name__ == '__main__':
    unittest.main()
