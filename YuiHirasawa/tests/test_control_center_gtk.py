"""Opt-in real GTK layout/animation checks (YUI_RUN_GTK_TESTS=1).

Only the Wayland positioning API and hardware backend are stubbed. All widget
allocation, drawing, frame-clock transitions and control signals use real GTK.
"""

import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.environ.get('YUI_RUN_GTK_TESTS') == '1', 'requires a GTK display')
class ControlCenterGtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk, GLib, Gtk
        cls.Gtk, cls.GLib = Gtk, GLib
        Gtk.init([])
        Gtk.Settings.get_default().set_property('gtk-enable-animations', True)
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'shell_widgets.py').read_text())
        helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in {'round_window_corners', 'slide_panel_from_right'}]
        namespace = {'Gtk': Gtk, 'GLib': GLib}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'shell_widgets.py', 'exec'), namespace)
        tree = ast.parse((root / 'control_center.py').read_text())
        control = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == 'StatusControls')
        namespace.update(Gdk=Gdk, GtkLayerShell=Mock(), backend=Mock(),
                         _prepare_launcher_dialog=lambda *args: None)
        exec(compile(ast.Module(body=[control], type_ignores=[]), 'control_center.py', 'exec'), namespace)
        cls.Controls = namespace['StatusControls']

    def setUp(self):
        self.parent = self.Gtk.Window()
        self.parent.set_default_size(600, 600)
        self.parent.show_all()
        self.refresh = patch.object(self.Controls, 'refresh', return_value=True)
        self.refresh.start()
        self.addCleanup(self.refresh.stop)
        self.controls = self.Controls(self.parent, 20, bottom_right=True)
        self.controls.data = {
            'audio': {'volume': 40, 'muted': False},
            'network': {'wifi': True, 'wireless': True, 'text': 'Connected to test network'},
            'bt': {'powered': True, 'adapters': ['test']},
            'pwr': {'text': 'Battery 80%'},
            'brightness': {'devices': [{'name': 'display', 'percent': 60}]},
        }
        self.addCleanup(self.parent.destroy)

    def settle(self, check):
        loop = self.GLib.MainLoop()
        timeout = [False]
        def expired():
            timeout[0] = True
            loop.quit()
            return False
        def poll():
            if check():
                loop.quit()
                return False
            return True
        deadline = self.GLib.timeout_add(2500, expired)
        poll_id = self.GLib.timeout_add(10, poll)
        loop.run()
        if timeout[0]:
            self.GLib.source_remove(poll_id)
        else:
            self.GLib.source_remove(deadline)
        self.assertFalse(timeout[0], 'GTK animation did not finish')

    def open_panel(self):
        self.controls.show()
        self.stack = self.controls.dialog.get_child()
        self.assertIsInstance(self.stack, self.Gtk.Stack)
        # GtkStack marks the transition running before the first layout frame.
        self.settle(lambda: self.stack.get_transition_running()
                    and self.stack.get_child_by_name('open').get_allocated_width() > 1)

    def test_real_controls_slide_at_constant_size_and_remain_usable(self):
        self.open_panel()
        sizes, offsets = [], []
        def finished():
            panel = self.stack.get_child_by_name('open')
            sizes.append((panel.get_allocated_width(), panel.get_allocated_height()))
            offsets.append(panel.get_parent_window().get_position()[0])
            return not self.stack.get_transition_running()
        self.settle(finished)
        self.assertEqual(len(set(sizes)), 1, sizes)
        self.assertTrue(any(x > 0 for x in offsets), offsets)
        self.assertEqual(offsets[-1], 0)
        self.assertTrue(self.controls.volume.get_mapped())
        self.assertTrue(self.controls.wifi.get_mapped())
        self.assertGreater(self.controls.volume.get_allocated_width(), 100)
        self.controls.volume.set_value(55)
        self.assertEqual(self.controls.volume.get_value(), 55)
        panel = self.stack.get_child_by_name('open')
        close = panel.get_children()[-1]
        # The close button must occupy the bottom of the panel, not be clipped.
        self.assertGreater(close.get_allocation().y, panel.get_allocated_height() / 2)
        self.assertGreater(close.get_allocated_height(), 1)
        output = os.environ.get('YUI_GTK_QA_DIR')
        if output:
            from gi.repository import Gdk
            path = Path(output)
            path.mkdir(parents=True, exist_ok=True)
            dialog = self.controls.dialog
            pixbuf = Gdk.pixbuf_get_from_window(dialog.get_window(), 0, 0,
                                              dialog.get_allocated_width(), dialog.get_allocated_height())
            pixbuf.savev(str(path / 'control-center.png'), 'png', [], [])
        close.clicked()
        self.assertIsNone(self.controls.dialog)

    def test_reopen_and_destroy_during_animation(self):
        self.open_panel()
        self.controls.dialog.hide()
        self.controls.show()
        self.settle(lambda: self.stack.get_transition_running())
        self.controls.dialog.destroy()
        self.assertIsNone(self.controls.dialog)
        self.open_panel()
        self.settle(lambda: not self.stack.get_transition_running())
        self.assertEqual(self.stack.get_visible_child_name(), 'open')

    def test_long_content_scrolls_and_switch_signals_still_work(self):
        self.controls.data['brightness']['devices'] = [
            {'name': f'display {i}', 'percent': 60} for i in range(12)]
        self.open_panel()
        self.settle(lambda: not self.stack.get_transition_running())
        panel = self.stack.get_child_by_name('open')
        scroller = panel.get_children()[0]
        adjustment = scroller.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        self.assertGreater(adjustment.get_value(), 0)
        with patch.object(self.controls, 'change') as change:
            self.controls.wifi.set_active(False)
            self.assertEqual(change.call_args.args[0], 'wifi')
        from gi.repository import Gdk
        self.assertTrue(self.controls._key(None, SimpleNamespace(keyval=Gdk.KEY_Escape)))
        self.assertIsNone(self.controls.dialog)


if __name__ == '__main__':
    unittest.main()
