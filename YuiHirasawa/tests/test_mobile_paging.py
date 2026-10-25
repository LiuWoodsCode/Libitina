"""Exercise pager movement and settling without a Wayland compositor."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def pager_class():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "mobile.py").read_text())
    launcher = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "ApplicationLauncher")
    names = {"_set_page", "_stop_page_snap", "_animate_page_snap", "_on_pager_unmap"}
    launcher.body = [node for node in launcher.body if isinstance(node, ast.FunctionDef)
                     and (node.name.startswith("_on_page_drag_") or node.name in names)]
    launcher.bases = []
    glib = SimpleNamespace(source_remove=Mock(), timeout_add=Mock(return_value=7),
                           get_monotonic_time=Mock(return_value=1_000_000),
                           SOURCE_REMOVE=False, SOURCE_CONTINUE=True)
    namespace = {"Gtk": SimpleNamespace(EventSequenceState=SimpleNamespace(CLAIMED="claimed")),
                 "GLib": glib}
    exec(compile(ast.Module(body=[launcher], type_ignores=[]), "mobile.py", "exec"), namespace)
    return namespace["ApplicationLauncher"], glib


class MobilePagingTests(unittest.TestCase):
    def setUp(self):
        cls, self.glib = pager_class()
        self.pager = cls()
        self.pager._current_page = 1
        self.pager._page_width = 400
        self.pager._page_dots = [Mock(), Mock(), Mock()]
        self.pager._snap_source = None
        self.position = 400
        self.pager._page_adjustment = Mock()
        self.pager._page_adjustment.get_value.side_effect = lambda: self.position
        self.pager._page_adjustment.set_value.side_effect = self.move
        self.gesture = Mock()
        self.pager._on_page_drag_begin(self.gesture, 0, 0)

    def move(self, value):
        self.position = value

    def drag(self, dx, dy=0):
        self.pager._on_page_drag_update(self.gesture, dx, dy)

    def release(self, dx, dy=0):
        self.pager._on_page_drag_end(self.gesture, dx, dy)

    def finish_animation(self):
        self.glib.get_monotonic_time.return_value = 1_220_000
        self.assertFalse(self.pager._animate_page_snap())

    def test_pages_follow_finger_in_both_directions_before_release(self):
        self.drag(-60)
        self.assertEqual(self.position, 460)
        self.drag(-150)
        self.assertEqual(self.position, 550)
        self.drag(40)
        self.assertEqual(self.position, 360)
        self.assertEqual(self.pager._current_page, 1)
        self.gesture.set_state.assert_called_once_with("claimed")

    def test_release_animates_to_next_page(self):
        self.drag(-100)
        self.release(-100)
        self.assertEqual(self.pager._current_page, 2)
        self.assertEqual(self.position, 500)
        self.glib.get_monotonic_time.return_value = 1_110_000
        self.assertTrue(self.pager._animate_page_snap())
        self.assertGreater(self.position, 500)
        self.assertLess(self.position, 800)
        self.finish_animation()
        self.assertEqual(self.position, 800)
        self.assertIsNone(self.pager._snap_source)

    def test_short_drag_snaps_back(self):
        self.drag(30)
        self.release(30)
        self.assertEqual(self.pager._current_page, 1)
        self.finish_animation()
        self.assertEqual(self.position, 400)

    def test_right_drag_goes_back(self):
        self.drag(100)
        self.release(100)
        self.finish_animation()
        self.assertEqual(self.position, 0)

    def test_taps_and_vertical_motion_do_not_move_or_claim(self):
        for dx, dy in ((0, 0), (5, 2), (30, 90)):
            self.drag(dx, dy)
            self.assertEqual(self.position, 400)
        self.gesture.set_state.assert_not_called()

    def test_drag_cannot_pass_first_or_last_page(self):
        self.drag(900)
        self.assertEqual(self.position, 0)
        self.drag(-900)
        self.assertEqual(self.position, 800)

    def test_cancel_snaps_back(self):
        self.drag(-100)
        self.pager._on_page_drag_cancel(self.gesture, None)
        self.finish_animation()
        self.assertEqual(self.position, 400)
        self.assertFalse(self.pager._page_drag_claimed)

    def test_new_drag_interrupts_animation_without_jumping(self):
        self.drag(-100)
        self.release(-100)
        self.pager._on_page_drag_begin(self.gesture, 0, 0)
        self.glib.source_remove.assert_called_once_with(7)
        self.assertEqual(self.position, 500)
        self.drag(-20)
        self.assertEqual(self.position, 520)

    def test_unmap_stops_animation_and_aligns_page(self):
        self.drag(-100)
        self.release(-100)
        self.pager._on_pager_unmap(Mock())
        self.glib.source_remove.assert_called_once_with(7)
        self.assertEqual(self.position, 800)


if __name__ == "__main__":
    unittest.main()
