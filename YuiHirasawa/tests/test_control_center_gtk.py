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
        from gi.repository import Gdk, Gio, GLib, Gtk
        cls.Gtk, cls.GLib = Gtk, GLib
        Gtk.init([])
        Gtk.Settings.get_default().set_property('gtk-enable-animations', True)
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'shell_widgets.py').read_text())
        helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in {'round_window_corners', 'animate_window_from_edge',
                                     'slide_panel_from_right', 'open_animated_panel', 'close_animated_panel'}]
        namespace = {'Gtk': Gtk, 'GLib': GLib, 'GtkLayerShell': Mock()}
        slides = ast.parse((root / 'panel_animation.py').read_text())
        sliding = [node for node in slides.body if isinstance(node, ast.FunctionDef)]
        exec(compile(ast.Module(body=sliding, type_ignores=[]), 'panel_animation.py', 'exec'), namespace)
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'shell_widgets.py', 'exec'), namespace)
        tree = ast.parse((root / 'control_center.py').read_text())
        control = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == 'StatusControls')
        namespace.update(Gdk=Gdk, GtkLayerShell=Mock(), backend=Mock(),
                         _prepare_launcher_dialog=lambda *args: None)
        exec(compile(ast.Module(body=[control], type_ignores=[]), 'control_center.py', 'exec'), namespace)
        cls.Controls = namespace['StatusControls']
        tree = ast.parse((root / 'desktop.py').read_text())
        launcher = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                        and node.name == 'ApplicationLauncher')
        apps = [SimpleNamespace(get_icon=lambda: None,
                                get_display_name=lambda i=i: f'Test App {i}')
                for i in range(24)]
        catalog = type('TestCatalog', (), {'_applications': lambda self: apps})
        namespace.update(Gio=Gio, ApplicationCatalog=catalog,
                         getpass=SimpleNamespace(getuser=lambda: 'Test user'),
                         attach_application_menu=lambda *args: None,
                         launch_application=Mock(return_value=False))
        exec(compile(ast.Module(body=[launcher], type_ignores=[]), 'desktop.py', 'exec'), namespace)
        cls.Launcher = namespace['ApplicationLauncher']
        cls.launch = namespace['launch_application']
        import html
        import urllib.parse
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf, Pango
        tree = ast.parse((root / 'notification.py').read_text())
        notifications = [node for node in tree.body if isinstance(node, ast.ClassDef)
                         and node.name in {'Notification', 'NotificationBanner', 'NotificationCenter'}]
        namespace.update(html=html, urllib=urllib, os=os, GdkPixbuf=GdkPixbuf, Pango=Pango,
                         GST_AVAILABLE=False, GTK_LAYER_SHELL_AVAILABLE=False, LOGGER=Mock())
        exec(compile(ast.Module(body=notifications, type_ignores=[]), 'notification.py', 'exec'), namespace)
        cls.Notification = namespace['Notification']
        cls.Banner = namespace['NotificationBanner']
        cls.Center = namespace['NotificationCenter']

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
        self.assertIsNotNone(self.controls.dialog)
        self.assertEqual(self.stack.get_transition_type(), self.Gtk.StackTransitionType.SLIDE_RIGHT)
        self.settle(lambda: self.controls.dialog is None)

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
        self.settle(lambda: self.controls.dialog is None)

    def test_launcher_slides_up_and_reopens_after_escape(self):
        launcher = self.Launcher()
        self.addCleanup(launcher.destroy)
        launcher.show_all()
        launcher.present()
        stack = launcher.get_child()
        self.assertIsInstance(stack, self.Gtk.Stack)
        self.assertEqual(stack.get_transition_type(), self.Gtk.StackTransitionType.NONE)
        panel = stack.get_child_by_name('open')
        self.settle(lambda: stack.get_transition_running()
                    and panel.get_allocated_width() > 1)
        self.assertEqual(stack.get_transition_type(), self.Gtk.StackTransitionType.SLIDE_UP)
        sizes, offsets = [], []
        def finished():
            sizes.append((panel.get_allocated_width(), panel.get_allocated_height()))
            offsets.append(panel.get_parent_window().get_position()[1])
            return not stack.get_transition_running()
        self.settle(finished)
        self.assertEqual(len(set(sizes)), 1, sizes)
        self.assertTrue(any(y > 0 for y in offsets), offsets)
        self.assertEqual(offsets[-1], 0)
        scroller = panel.get_children()[-1]
        app_list = scroller.get_child().get_child()
        button = app_list.get_children()[0].get_child()
        self.assertTrue(button.get_mapped())
        self.launch.reset_mock()
        button.clicked()
        self.launch.assert_called_once()
        from gi.repository import Gdk
        launcher._on_key_press(launcher, SimpleNamespace(keyval=Gdk.KEY_Escape))
        self.assertTrue(launcher.get_visible())
        self.assertEqual(stack.get_transition_type(), self.Gtk.StackTransitionType.SLIDE_DOWN)
        self.settle(lambda: not launcher.get_visible())
        launcher.toggle()
        self.settle(lambda: stack.get_transition_running())
        self.settle(lambda: not stack.get_transition_running())
        self.assertEqual(stack.get_visible_child_name(), 'open')

    def test_reopening_control_center_cancels_pending_close(self):
        self.open_panel()
        self.settle(lambda: not self.stack.get_transition_running())
        dialog = self.controls.dialog
        self.controls._dismiss_dialog()
        self.assertTrue(self.stack.get_transition_running())
        self.controls.show()
        self.settle(lambda: not self.stack.get_transition_running())
        self.assertIs(self.controls.dialog, dialog)
        self.assertTrue(dialog.get_visible())
        self.assertEqual(self.stack.get_visible_child_name(), 'open')

    def test_close_with_animations_disabled_does_not_wait(self):
        self.open_panel()
        self.settle(lambda: not self.stack.get_transition_running())
        settings = self.Gtk.Settings.get_default()
        settings.set_property('gtk-enable-animations', False)
        try:
            self.controls._dismiss_dialog()
            self.assertIsNone(self.controls.dialog)
        finally:
            settings.set_property('gtk-enable-animations', True)

    def test_notification_slides_from_right_without_mapping_empty_banner(self):
        banner = self.Banner()
        self.addCleanup(banner.destroy)
        self.assertFalse(banner.get_mapped())
        notice = self.Notification('Test app', '', 'Test notification',
                                   'A long message that wraps across multiple lines. ' * 8,
                                   0, [('test', 'Test action')], {})
        banner.set_notification(notice)
        invoked, dismissed = Mock(), Mock()
        banner.set_handlers(Mock(), invoked, dismissed)
        width, height = banner.get_layout_size()
        from gi.repository import Gdk
        banner.place(20, 20, width, height, Gdk.Rectangle(x=0, y=0, width=1200, height=900))
        banner.show()
        stack = banner.get_child()
        self.settle(lambda: stack.get_transition_running()
                    and stack.get_child_by_name('open').get_allocated_width() > 1)
        self.assertEqual(stack.get_transition_type(), self.Gtk.StackTransitionType.SLIDE_LEFT)
        self.settle(lambda: not stack.get_transition_running())
        self.assertGreater(banner.message.get_allocated_height(), 20)
        self.assertTrue(banner.title.get_mapped())
        self.assertFalse(banner.icon.get_visible())
        banner.actions_box.get_children()[0].clicked()
        invoked.assert_called_once_with('test')
        banner.close_button.clicked()
        dismissed.assert_called_once_with()

    def test_visible_notification_updates_without_restarting_slide(self):
        center = self.Center()
        notice = self.Notification('Test app', '', 'First', 'Body', 0, [], {})
        center.show_notification(1, notice)
        banner = center.notifications_by_id[1]['banner']
        self.addCleanup(banner.destroy)
        stack = banner.get_child()
        self.settle(lambda: stack.get_transition_running())
        self.settle(lambda: not stack.get_transition_running())
        notice.summary = 'Updated'
        center.show_notification(1, notice)
        self.assertIs(center.notifications_by_id[1]['banner'], banner)
        self.assertFalse(stack.get_transition_running())
        self.assertEqual(banner.title.get_text(), 'Updated')
        center.show_notification(2, notice)
        second = center.notifications_by_id[2]['banner']
        self.addCleanup(second.destroy)
        self.assertNotEqual(center._target_y(0), center._target_y(1))
        self.assertTrue(center.hide_notification(1))
        self.assertTrue(center.hide_notification(2))


if __name__ == '__main__':
    unittest.main()
