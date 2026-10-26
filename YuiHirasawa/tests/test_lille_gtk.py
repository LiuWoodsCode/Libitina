"""Opt-in real GTK checks for Lille (YUI_RUN_GTK_TESTS=1)."""

import os
import ast
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lille


@unittest.skipUnless(os.environ.get('YUI_RUN_GTK_TESTS') == '1', 'requires a GTK display')
class LilleProtectionGtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        Gtk.init([])
        cls.Gtk = Gtk

    def setUp(self):
        import gi
        import gi.repository
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'state.json'
        self.layers = Mock()
        require = gi.require_version
        self.patch_require = patch.object(gi, 'require_version', side_effect=lambda name, version:
                                           None if name == 'GtkLayerShell' else require(name, version))
        self.patch_layers = patch.dict(gi.repository.__dict__, {'GtkLayerShell': self.layers})
        self.patch_require.start()
        self.patch_layers.start()
        self.addCleanup(self.patch_require.stop)
        self.addCleanup(self.patch_layers.stop)
        self.power = True
        self.original = self.Gtk.Window(title='Normal shell')
        self.original.show_all()
        self.addCleanup(self.original.destroy)
        self.config = dict(enable_lille=True, lille_location='Library desk')

    def controller(self, owner=True):
        controller = lille.LilleProtection(owner=owner, config=self.config,
                                         state_path=self.path, reader=lambda: self.power)
        self.addCleanup(controller.close)
        return controller

    def test_unplug_hides_windows_and_blocks_new_maps_and_replug(self):
        guard = self.controller()
        guard._poll()
        self.assertTrue(self.original.get_visible())
        self.power = False
        guard._poll()
        self.assertFalse(self.original.get_visible())
        self.assertTrue(guard.overlays)
        for window in guard.overlays.values():
            self.assertTrue(window.get_visible())
            labels = window.get_child().get_children()[-1].get_children()
            self.assertEqual(labels[0].get_text(), 'Please return to Library desk')
            self.assertEqual(labels[1].get_text(), lille.MESSAGE)
            self.assertGreater(labels[0].get_style_context().get_font(self.Gtk.StateFlags.NORMAL).get_size(),
                               labels[1].get_style_context().get_font(self.Gtk.StateFlags.NORMAL).get_size())
        self.layers.set_keyboard_mode.assert_called_with(
            next(reversed(guard.overlays.values())), self.layers.KeyboardMode.EXCLUSIVE)
        self.original.show_all()
        self.assertFalse(self.original.get_visible())
        late = self.Gtk.Window(title='Late notification')
        self.addCleanup(late.destroy)
        late.show_all()
        self.assertFalse(late.get_visible())
        self.power = True
        guard._poll()
        self.assertTrue(guard.state.triggered)
        self.assertFalse(self.original.get_visible())
        self.assertTrue(all(window.get_visible() for window in guard.overlays.values()))
        output = os.environ.get('YUI_GTK_QA_DIR')
        if output:
            from gi.repository import GLib, Gdk
            # Give real GTK a layout/draw turn before capturing the message.
            loop = GLib.MainLoop()
            window = next(iter(guard.overlays.values()))
            window.resize(360, 640)
            GLib.timeout_add(100, lambda: (loop.quit(), False)[1])
            loop.run()
            window = next(iter(guard.overlays.values()))
            pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0,
                                              window.get_allocated_width(), window.get_allocated_height())
            path = Path(output)
            path.mkdir(parents=True, exist_ok=True)
            pixbuf.savev(str(path / 'lille.png'), 'png', [], [])

    def test_notification_observer_hides_without_creating_overlay(self):
        observer = self.controller(owner=False)
        self.assertFalse(self.path.exists())
        state = lille.LilleState(self.path, owner=True, location='Library desk', connected=True)
        state.update(False)
        observer._poll()
        self.assertFalse(self.original.get_visible())
        self.assertFalse(observer.overlays)
        self.original.show_all()
        self.assertFalse(self.original.get_visible())

    def test_starting_unplugged_does_not_hide_windows(self):
        self.power = False
        guard = self.controller()
        self.power = True
        guard._poll()
        self.power = False
        guard._poll()
        self.assertFalse(guard.overlays)
        self.assertTrue(self.original.get_visible())

    def test_record_only_trip_leaves_windows_visible(self):
        self.config['lille_disable_screen'] = False
        guard = self.controller()
        self.power = False
        guard._poll()
        self.assertTrue(guard.state.triggered)
        self.assertFalse(guard.overlays)
        self.assertTrue(self.original.get_visible())
        self.original.show_all()
        self.assertTrue(self.original.get_visible())

    def test_proximity_rules_reach_fullscreen_controller(self):
        self.config.update(lille_ssids=['Store'], lille_require_charger=False)
        self.power = True
        probe = Mock()
        probe.read.side_effect = [True, None, False]
        with patch.object(lille, 'NetworkProbe', return_value=probe):
            guard = self.controller()
            guard._poll()
            self.assertTrue(guard.state.armed)
            self.assertFalse(guard.state.triggered)
            guard._poll()
            self.assertFalse(guard.state.triggered)
            guard._poll()
            self.assertTrue(guard.state.triggered)
            self.assertTrue(guard.overlays)
            self.assertFalse(self.original.get_visible())

    def test_persistent_trip_is_shown_without_waiting_for_charger_or_network(self):
        self.config.update(lille_persist_trip=True, lille_trip_value=True, lille_ssids=['Store'])
        guard = self.controller()
        self.assertTrue(guard.state.triggered)
        guard._poll()
        self.assertTrue(guard.overlays)
        self.assertFalse(self.original.get_visible())

    def test_message_is_centered_and_unclipped_at_logical_screen_sizes(self):
        from gi.repository import GLib
        self.config['lille_location'] = 'demo station'
        guard = self.controller()
        self.power = False
        guard._poll()
        window = next(iter(guard.overlays.values()))
        frame = window.get_child()
        content = frame.get_children()[-1]
        # Wrapped labels must never enlarge the layer surface's requisition.
        _minimum, natural = frame.get_preferred_size()
        self.assertLessEqual(natural.height, 1)
        self.assertLessEqual(natural.width, 1)
        # Test the real GTK layout without the native window manager's sizing
        # rules. The layer-shell API is stubbed on this Mac, so the original
        # full-monitor native window is not a faithful resize test surface.
        window.remove(frame)
        for name in ('_source', '_initial'):
            source = getattr(guard, name)
            if source is not None:
                GLib.source_remove(source)
                setattr(guard, name, None)
        guard.GObject.signal_remove_emission_hook(guard._map_signal, guard._hook)
        guard._hook = None
        for width, height in ((320, 568), (360, 720), (640, 320), (1280, 720)):
            with self.subTest(logical_size=(width, height)):
                host = self.Gtk.OffscreenWindow()
                host.get_style_context().add_class('lille')
                host.set_size_request(width, height)
                host.add(frame)
                host.show_all()
                loop = GLib.MainLoop()
                GLib.timeout_add(50, lambda: (loop.quit(), False)[1])
                loop.run()
                try:
                    self.assertEqual(frame.get_allocated_width(), width)
                    self.assertEqual(frame.get_allocated_height(), height)
                    x, y = content.translate_coordinates(frame, 0, 0)
                    self.assertAlmostEqual(x + content.get_allocated_width() / 2, width / 2, delta=1)
                    self.assertAlmostEqual(y + content.get_allocated_height() / 2, height / 2, delta=1)
                    for label in content.get_children():
                        self.assertTrue(label.get_mapped())
                        lx, ly = label.translate_coordinates(frame, 0, 0)
                        self.assertGreaterEqual(lx, 0)
                        self.assertGreaterEqual(ly, 0)
                        self.assertLessEqual(lx + label.get_allocated_width(), width)
                        self.assertLessEqual(ly + label.get_allocated_height(), height)
                finally:
                    host.remove(frame)
                    host.destroy()


    def test_settings_visibility_validation_and_round_trip(self):
        import settings
        source = (Path(__file__).resolve().parents[2] /
                  'Applications/Production/Yui/YuiSettings/main.py').read_text()
        tree = ast.parse(source)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'SettingsWindow')
        cls.bases = []
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                    and node.name in {'_add_lille_page', '_save_lille', '_clear_lille_trip', '_make_sidebar'}]
        make_page = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == 'make_page')
        namespace = {'Gtk': self.Gtk, 'load_config': settings.load_config,
                     'save_config': settings.save_config}
        exec(compile(ast.Module(body=[make_page, cls], type_ignores=[]), 'YuiSettings/main.py', 'exec'), namespace)
        with patch.object(settings, 'CONFIG_PATH', self.path.parent / 'config.json'):
            view = namespace['SettingsWindow']()
            view._stack = self.Gtk.Stack()
            view._on_sidebar_row_selected = Mock()
            view._on_sidebar_row_activated = Mock()
            view._add_lille_page()
            self.assertIsNone(view._stack.get_child_by_name('lille'))
            sidebar = view._make_sidebar()
            self.assertNotIn('lille', [row.page_name for row in sidebar.get_children()])
            config = settings.load_config()
            config['enable_lille_config'] = True
            settings.save_config(config)
            view._add_lille_page()
            self.assertIsNotNone(view._stack.get_child_by_name('lille'))
            sidebar = view._make_sidebar()
            self.assertIn('lille', [row.page_name for row in sidebar.get_children()])
            view._lille_enabled.set_active(True)
            view._lille_location.set_text('Reception desk')
            view._lille_networks['lille_ssids'].get_buffer().set_text('Store East\nStore West')
            view._lille_networks['lille_bssids'].get_buffer().set_text('aa:bb:cc:dd:ee:ff')
            view._lille_require_charger.set_active(False)
            view._lille_delay.set_value(30)
            view._lille_disable_screen.set_active(False)
            view._lille_persist.set_active(True)
            view._save_lille()
            saved = settings.load_config()
            self.assertTrue(saved['enable_lille'])
            self.assertEqual(saved['lille_location'], 'Reception desk')
            self.assertEqual(saved['lille_ssids'], ['Store East', 'Store West'])
            self.assertEqual(saved['lille_bssids'], ['AA:BB:CC:DD:EE:FF'])
            self.assertEqual(saved['lille_delay_seconds'], 30)
            self.assertFalse(saved['lille_require_charger'])
            self.assertFalse(saved['lille_disable_screen'])
            self.assertTrue(saved['lille_persist_trip'])
            config = settings.load_config()
            config['lille_trip_value'] = True
            settings.save_config(config)
            view._save_lille()
            self.assertTrue(settings.load_config()['lille_trip_value'])
            view._clear_lille_trip()
            self.assertFalse(settings.load_config()['lille_trip_value'])
            view._lille_networks['lille_bssids'].get_buffer().set_text('invalid')
            view._save_lille()
            self.assertIn('BSSID', view._lille_status.get_text())
            self.assertEqual(settings.load_config()['lille_bssids'], ['AA:BB:CC:DD:EE:FF'])
            view._lille_networks['lille_bssids'].get_buffer().set_text('')
            view._lille_location.set_text('')
            view._save_lille()
            self.assertIn('nonempty', view._lille_status.get_text())


if __name__ == '__main__':
    unittest.main()
