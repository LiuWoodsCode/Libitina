"""Stable GTK panel slides shared by the shell and notification daemon."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk


def sliding_stack(window: Gtk.Window, transition, reverse, duration_ms: int = 240) -> Gtk.Stack:
    """Slide the existing child without changing its size during animation."""
    content = window.get_child()
    window.remove(content)
    stack = Gtk.Stack()
    stack.get_style_context().add_class("shell-slide-stack")
    stack.set_homogeneous(True)
    stack.set_interpolate_size(False)
    stack.set_transition_duration(duration_ms)
    blank = Gtk.Box()
    blank.show()
    content.show()
    stack.add_named(blank, "closed")
    stack.add_named(content, "open")
    stack.set_visible_child_name("closed")
    window.add(stack)
    pending = None
    close_action = None
    window._shell_panel_closing = False

    def cancel_pending():
        nonlocal pending
        if pending is not None:
            GLib.source_remove(pending)
            pending = None

    def completed(*_args):
        nonlocal close_action
        if close_action is not None and not stack.get_transition_running():
            action, close_action = close_action, None
            action()

    def close(destroy=False):
        nonlocal close_action
        if window._shell_panel_closing:
            return
        cancel_pending()
        window._shell_panel_closing = True
        close_action = window.destroy if destroy else window.hide
        stack.set_transition_type(reverse)
        stack.set_visible_child_name("closed")
        # With animations disabled (or before the opening frame), no
        # transition-running notification is guaranteed.
        completed()

    def reopen():
        nonlocal close_action
        close_action = None
        window._shell_panel_closing = False
        cancel_pending()
        if window.get_mapped():
            stack.set_transition_type(transition)
            stack.set_visible_child_name("open")

    window._close_shell_panel = close
    window._reopen_shell_panel = reopen
    stack.connect("notify::transition-running", completed)

    def stop(_widget, *_args):
        nonlocal close_action
        close_action = None
        window._shell_panel_closing = False
        cancel_pending()
        # Reset immediately so hide/reopen never animates the reset itself.
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        stack.set_visible_child_name("closed")

    def mapped(widget):
        nonlocal pending
        stop(widget)

        def reveal():
            nonlocal pending
            pending = None
            if widget.get_mapped():
                stack.set_transition_type(transition)
                stack.set_visible_child_name("open")
            return GLib.SOURCE_REMOVE

        pending = GLib.idle_add(reveal)

    window.connect("map", mapped)
    window.connect("unmap", stop)
    window.connect("destroy", stop)
    return stack
