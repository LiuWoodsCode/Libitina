"""Verify panel animation keeps its Wayland surface visible."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class DesktopAnimationTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "shell_widgets.py").read_text())
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "animate_window_from_edge")
        self.layer = Mock()
        self.layer.Edge.RIGHT = "right"
        self.revealer = Mock()
        self.stack = Mock()
        self.glib = SimpleNamespace(SOURCE_REMOVE=False, SOURCE_CONTINUE=True,
                                    idle_add=Mock(return_value=7),
                                    source_remove=Mock(), timeout_add=Mock(return_value=9),
                                    get_monotonic_time=Mock(return_value=1_000_000))
        namespace = {
            "Gtk": SimpleNamespace(Window=object, Stack=Mock(return_value=self.stack),
                                   Box=Mock(), CssProvider=Mock(),
                                   STYLE_PROVIDER_PRIORITY_APPLICATION=600,
                                   StackTransitionType=SimpleNamespace(NONE="none", SLIDE_LEFT="left", SLIDE_UP="up", SLIDE_RIGHT="right", SLIDE_DOWN="down"),
                                   Revealer=Mock(return_value=self.revealer),
                                   RevealerTransitionType=SimpleNamespace(
                                       SLIDE_LEFT="left", SLIDE_UP="up", SLIDE_RIGHT="right", SLIDE_DOWN="down")),
            "GtkLayerShell": self.layer, "GLib": self.glib}
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "slide_panel_from_right")
        namespace["round_window_corners"] = Mock()
        slide_tree = ast.parse((Path(__file__).resolve().parents[1] / "panel_animation.py").read_text())
        slide = next(node for node in slide_tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "sliding_stack")
        exec(compile(ast.Module(body=[slide], type_ignores=[]), "panel_animation.py", "exec"), namespace)
        exec(compile(ast.Module(body=[function, helper], type_ignores=[]), "shell_widgets.py", "exec"), namespace)
        self.animate = namespace["animate_window_from_edge"]
        self.slide = namespace["slide_panel_from_right"]
        self.window = Mock()
        self.window.get_mapped.return_value = True

    def connect(self, edge):
        if edge == "right":
            self.slide(self.window)
        else:
            self.animate(self.window, edge)
        return {call.args[0]: call.args[1] for call in self.window.connect.call_args_list}

    def test_control_center_uses_stable_stack_without_offscreen_margins(self):
        signals = self.connect("right")
        self.stack.set_homogeneous.assert_called_once_with(True)
        self.stack.set_interpolate_size.assert_called_once_with(False)
        self.stack.set_transition_duration.assert_called_once_with(240)
        self.stack.set_visible_child_name.assert_called_with("closed")
        signals["map"](self.window)
        self.glib.idle_add.call_args.args[0]()
        self.stack.set_transition_type.assert_called_with("left")
        self.stack.set_visible_child_name.assert_called_with("open")
        self.layer.set_margin.assert_not_called()
        self.window.set_size_request.assert_not_called()

    def test_control_center_cancels_pending_slide_when_hidden(self):
        signals = self.connect("right")
        signals["map"](self.window)
        signals["unmap"](self.window)
        self.glib.source_remove.assert_called_once_with(7)
        self.stack.set_transition_type.assert_called_with("none")
        self.stack.set_visible_child_name.assert_called_with("closed")
        signals["map"](self.window)
        self.glib.idle_add.call_args.args[0]()
        self.stack.set_visible_child_name.assert_called_with("open")

    def test_launcher_reveals_upward(self):
        signals = self.connect("bottom")
        self.stack.set_homogeneous.assert_called_once_with(True)
        self.stack.set_transition_duration.assert_called_once_with(240)
        signals["map"](self.window)
        self.glib.idle_add.call_args.args[0]()
        self.stack.set_transition_type.assert_called_with("up")
        self.stack.set_visible_child_name.assert_called_with("open")

    def test_hiding_cancels_pending_reveal_and_reopening_restarts(self):
        signals = self.connect("bottom")
        signals["map"](self.window)
        signals["unmap"](self.window)
        self.glib.source_remove.assert_called_once_with(7)
        self.stack.set_visible_child_name.assert_called_with("closed")
        signals["map"](self.window)
        self.glib.idle_add.call_args.args[0]()
        self.stack.set_visible_child_name.assert_called_with("open")

    def test_existing_hidden_control_center_is_shown_again(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "control_center.py").read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "StatusControls")
        show = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                    and node.name == "show")
        namespace = {}
        namespace["open_animated_panel"] = Mock()
        exec(compile(ast.Module(body=[show], type_ignores=[]), "control_center.py", "exec"), namespace)
        controls = Mock()
        namespace["show"](controls)
        namespace["open_animated_panel"].assert_called_once_with(controls.dialog)
        controls._create_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
