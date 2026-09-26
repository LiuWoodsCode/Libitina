#!/usr/bin/env python3
"""A small, touch-friendly Wayland lock screen for the Yui shell.

The passcode is checked against the current Unix user's password through PAM.
No password or PIN is stored by this program.  The PAM service defaults to
``phosh`` and can be overridden with the YUI_PAM_SERVICE environment variable.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import datetime as dt
import os
import pwd
import threading
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell


PAM_SUCCESS = 0
PAM_SYSTEM_ERR = 4
PAM_BUF_ERR = 5
PAM_CONV_ERR = 19
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
PAM_ERROR_MSG = 3
PAM_TEXT_INFO = 4


class PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class PamResponse(ctypes.Structure):
    # Keep resp as an opaque pointer: PAM takes ownership and frees it with
    # free(3), so exposing it as c_char_p would make ctypes copy its contents.
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


PamConversationFunction = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.POINTER(PamMessage)),
    ctypes.POINTER(ctypes.POINTER(PamResponse)),
    ctypes.c_void_p,
)


class PamConversation(ctypes.Structure):
    _fields_ = [("conv", PamConversationFunction), ("appdata_ptr", ctypes.c_void_p)]


@dataclass(frozen=True)
class AuthenticationResult:
    accepted: bool
    message: str


class PamAuthenticator:
    """Minimal libpam client following Phosh's lock-screen auth flow."""

    def __init__(self, service: str = "phosh") -> None:
        library_name = ctypes.util.find_library("pam")
        if not library_name:
            raise RuntimeError("libpam is not installed")

        self._pam = ctypes.CDLL(library_name)
        self._libc = ctypes.CDLL(None)
        self._service = service.encode("utf-8")
        self._username = pwd.getpwuid(os.getuid()).pw_name.encode("utf-8")

        self._pam.pam_start.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(PamConversation),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._pam.pam_start.restype = ctypes.c_int
        self._pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_authenticate.restype = ctypes.c_int
        self._pam.pam_acct_mgmt.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_acct_mgmt.restype = ctypes.c_int
        self._pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_end.restype = ctypes.c_int
        self._pam.pam_strerror.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._pam.pam_strerror.restype = ctypes.c_char_p

        self._libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        self._libc.calloc.restype = ctypes.c_void_p
        self._libc.strdup.argtypes = [ctypes.c_char_p]
        self._libc.strdup.restype = ctypes.c_void_p
        self._libc.free.argtypes = [ctypes.c_void_p]
        self._libc.free.restype = None

    def authenticate(self, passcode: str) -> AuthenticationResult:
        if not passcode or not passcode.isascii() or not passcode.isdecimal():
            return AuthenticationResult(False, "Enter a numerical passcode")

        secret = bytearray(passcode, "utf-8")
        handle = ctypes.c_void_p()
        callback = self._make_conversation(secret)
        conversation = PamConversation(callback, None)
        status = PAM_SYSTEM_ERR

        try:
            status = self._pam.pam_start(
                self._service,
                self._username,
                ctypes.byref(conversation),
                ctypes.byref(handle),
            )
            if status != PAM_SUCCESS:
                return AuthenticationResult(False, self._error(handle, status))

            status = self._pam.pam_authenticate(handle, 0)
            if status != PAM_SUCCESS:
                return AuthenticationResult(False, "Incorrect passcode")

            status = self._pam.pam_acct_mgmt(handle, 0)
            if status != PAM_SUCCESS:
                return AuthenticationResult(False, self._error(handle, status))

            return AuthenticationResult(True, "Unlocked")
        finally:
            for index in range(len(secret)):
                secret[index] = 0
            if handle.value:
                self._pam.pam_end(handle, status)

    def _make_conversation(self, secret: bytearray) -> PamConversationFunction:
        libc = self._libc

        @PamConversationFunction
        def converse(message_count, messages, responses, _appdata):
            if message_count <= 0 or not messages or not responses:
                return PAM_CONV_ERR

            raw = libc.calloc(message_count, ctypes.sizeof(PamResponse))
            if not raw:
                return PAM_BUF_ERR
            reply_array = ctypes.cast(raw, ctypes.POINTER(PamResponse))

            for index in range(message_count):
                style = messages[index].contents.msg_style
                if style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                    duplicated = libc.strdup(bytes(secret))
                    if not duplicated:
                        for allocated in range(index):
                            if reply_array[allocated].resp:
                                libc.free(reply_array[allocated].resp)
                        libc.free(raw)
                        return PAM_BUF_ERR
                    reply_array[index].resp = duplicated
                elif style not in (PAM_ERROR_MSG, PAM_TEXT_INFO):
                    for allocated in range(index):
                        if reply_array[allocated].resp:
                            libc.free(reply_array[allocated].resp)
                    libc.free(raw)
                    return PAM_CONV_ERR

            responses[0] = reply_array
            return PAM_SUCCESS

        return converse

    def _error(self, handle: ctypes.c_void_p, status: int) -> str:
        text = self._pam.pam_strerror(handle, status)
        return text.decode("utf-8", errors="replace") if text else "PAM error"


class LockScreen(Gtk.Window):
    SWIPE_DISTANCE = 64
    MAX_PASSCODE_LENGTH = 32

    def __init__(self, authenticator: PamAuthenticator) -> None:
        super().__init__(title="Yui lock screen")
        self._authenticator = authenticator
        self._authenticating = False

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.connect("delete-event", lambda *_args: True)
        self.connect("key-press-event", self._on_key_press)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.RIGHT,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_namespace(self, "yui-locker")

        self._install_css()
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP)
        self._stack.set_transition_duration(260)
        self.add(self._stack)

        self._clock_label = Gtk.Label()
        self._date_label = Gtk.Label()
        self._build_clock_page()
        self._build_unlock_page()

        self._drag = Gtk.GestureDrag.new(self)
        self._drag.connect("drag-end", self._on_drag_end)

        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)

    def _build_clock_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        page.set_halign(Gtk.Align.FILL)
        page.set_valign(Gtk.Align.FILL)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.pack_start(spacer, True, True, 0)

        self._clock_label.get_style_context().add_class("lock-clock")
        self._date_label.get_style_context().add_class("lock-date")
        page.pack_start(self._clock_label, False, False, 0)
        page.pack_start(self._date_label, False, False, 0)

        prompt = Gtk.Label(label="⌃\nSwipe up to unlock")
        prompt.set_justify(Gtk.Justification.CENTER)
        prompt.set_margin_bottom(32)
        prompt.set_margin_top(80)
        prompt.get_style_context().add_class("swipe-prompt")
        page.pack_end(prompt, False, False, 0)

        self._stack.add_named(page, "clock")

    def _build_unlock_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_halign(Gtk.Align.CENTER)
        page.set_valign(Gtk.Align.CENTER)
        page.set_margin_top(16)
        page.set_margin_bottom(16)
        page.set_margin_start(28)
        page.set_margin_end(28)

        heading = Gtk.Label(label="Enter Passcode")
        heading.get_style_context().add_class("unlock-heading")
        page.pack_start(heading, False, False, 0)

        self._entry = Gtk.Entry()
        self._entry.set_visibility(False)
        self._entry.set_invisible_char("●")
        self._entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._entry.set_max_length(self.MAX_PASSCODE_LENGTH)
        self._entry.set_alignment(0.5)
        self._entry.set_placeholder_text("PIN")
        self._entry.connect("insert-text", self._filter_inserted_text)
        self._entry.connect("changed", self._on_entry_changed)
        self._entry.connect("activate", self._submit)
        self._entry.get_style_context().add_class("pin-entry")
        page.pack_start(self._entry, False, False, 0)

        self._status = Gtk.Label(label="")
        self._status.set_size_request(-1, 24)
        self._status.get_style_context().add_class("unlock-status")
        page.pack_start(self._status, False, False, 0)

        keypad = Gtk.Grid()
        keypad.set_row_spacing(8)
        keypad.set_column_spacing(8)
        keypad.set_column_homogeneous(True)
        keypad.set_row_homogeneous(True)
        for index, digit in enumerate("123456789"):
            keypad.attach(self._number_button(digit), index % 3, index // 3, 1, 1)

        empty = Gtk.Label()
        keypad.attach(empty, 0, 3, 1, 1)
        keypad.attach(self._number_button("0"), 1, 3, 1, 1)
        delete = Gtk.Button(label="⌫")
        delete.set_tooltip_text("Delete")
        delete.connect("clicked", self._delete_digit)
        delete.get_style_context().add_class("keypad-button")
        keypad.attach(delete, 2, 3, 1, 1)
        page.pack_start(keypad, True, True, 0)

        self._submit_button = Gtk.Button(label="Submit")
        self._submit_button.set_sensitive(False)
        self._submit_button.set_can_default(True)
        self._submit_button.connect("clicked", self._submit)
        self._submit_button.get_style_context().add_class("submit-button")
        page.pack_start(self._submit_button, False, False, 0)

        back = Gtk.Button(label="Back")
        back.set_relief(Gtk.ReliefStyle.NONE)
        back.connect("clicked", self._show_clock)
        back.get_style_context().add_class("back-button")
        page.pack_start(back, False, False, 0)

        self._stack.add_named(page, "unlock")

    def _number_button(self, digit: str) -> Gtk.Button:
        button = Gtk.Button(label=digit)
        button.connect("clicked", self._append_digit, digit)
        button.get_style_context().add_class("keypad-button")
        return button

    def _append_digit(self, _button: Gtk.Button, digit: str) -> None:
        if self._authenticating:
            return
        text = self._entry.get_text()
        if len(text) < self.MAX_PASSCODE_LENGTH:
            self._entry.set_text(text + digit)
            self._entry.set_position(-1)

    def _delete_digit(self, _button: Gtk.Button) -> None:
        if not self._authenticating:
            self._entry.set_text(self._entry.get_text()[:-1])

    def _filter_inserted_text(
        self, entry: Gtk.Entry, text: str, _length: int, position: object
    ) -> None:
        filtered = "".join(character for character in text if character in "0123456789")
        if filtered == text:
            return
        entry.stop_emission_by_name("insert-text")
        if filtered:
            current = entry.get_text()
            cursor = entry.get_position()
            entry.set_text(current[:cursor] + filtered + current[cursor:])
            entry.set_position(cursor + len(filtered))

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        if not self._authenticating:
            self._status.set_text("")
        self._submit_button.set_sensitive(
            bool(entry.get_text()) and not self._authenticating
        )

    def _on_drag_end(
        self, _gesture: Gtk.GestureDrag, offset_x: float, offset_y: float
    ) -> None:
        if self._stack.get_visible_child_name() == "clock":
            if offset_y <= -self.SWIPE_DISTANCE and abs(offset_y) > abs(offset_x):
                self._show_unlock()

    def _show_unlock(self) -> None:
        self._stack.set_visible_child_name("unlock")
        GLib.idle_add(self._focus_entry)

    def _focus_entry(self) -> bool:
        self._entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def _show_clock(self, _button: Gtk.Button | None = None) -> None:
        if self._authenticating:
            return
        self._entry.set_text("")
        self._status.set_text("")
        self._stack.set_visible_child_name("clock")

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        key = Gdk.keyval_name(event.keyval) or ""
        if self._stack.get_visible_child_name() == "clock":
            reveals_unlock = (
                key in ("space", "Return", "KP_Enter", "Up")
                or key.startswith("KP_")
                or key.isdecimal()
            )
            if reveals_unlock:
                self._show_unlock()
                if key.isdecimal():
                    self._append_digit(None, key)
                elif key.startswith("KP_") and key[3:].isdecimal():
                    self._append_digit(None, key[3:])
                return True
            return False
        if key == "Escape":
            self._show_clock()
            return True
        return False

    def _submit(self, _widget: Gtk.Widget) -> None:
        if self._authenticating:
            return
        passcode = self._entry.get_text()
        if not passcode:
            return

        self._authenticating = True
        self._entry.set_sensitive(False)
        self._submit_button.set_sensitive(False)
        self._status.set_text("Checking…")
        threading.Thread(
            target=self._authenticate_worker,
            args=(passcode,),
            name="YuiPamAuthentication",
            daemon=True,
        ).start()

    def _authenticate_worker(self, passcode: str) -> None:
        try:
            result = self._authenticator.authenticate(passcode)
        except Exception as error:  # Keep a broken PAM setup from killing the lock UI.
            result = AuthenticationResult(False, f"Authentication error: {error}")
        GLib.idle_add(self._authentication_finished, result)

    def _authentication_finished(self, result: AuthenticationResult) -> bool:
        self._authenticating = False
        if result.accepted:
            self.destroy()
            Gtk.main_quit()
            return GLib.SOURCE_REMOVE

        self._entry.set_text("")
        self._entry.set_sensitive(True)
        self._status.set_text(result.message)
        self._entry.get_style_context().add_class("authentication-failed")
        GLib.timeout_add(450, self._finish_failure_feedback)
        self._entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def _finish_failure_feedback(self) -> bool:
        self._entry.get_style_context().remove_class("authentication-failed")
        return GLib.SOURCE_REMOVE

    def _update_clock(self) -> bool:
        now = dt.datetime.now().astimezone()
        self._clock_label.set_text(now.strftime("%H:%M"))
        self._date_label.set_text(now.strftime("%A, %B %-d"))
        return GLib.SOURCE_CONTINUE

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            window, window.background { background: #000; color: #fff; }
            .lock-clock { font-size: 64px; font-weight: 300; color: #fff; }
            .lock-date { font-size: 18px; color: rgba(255,255,255,.78); }
            .swipe-prompt { font-size: 14px; color: rgba(255,255,255,.62); }
            .unlock-heading { font-size: 24px; font-weight: 600; }
            .pin-entry {
                min-height: 42px; font-size: 22px; letter-spacing: 7px;
                color: #fff; background: rgba(255,255,255,.10);
                border: 1px solid rgba(255,255,255,.30); border-radius: 10px;
            }
            .pin-entry.authentication-failed { border-color: #ff5d68; }
            .unlock-status { color: #ff8991; font-size: 13px; }
            .keypad-button {
                min-width: 62px; min-height: 48px; font-size: 20px;
                color: #fff; background: rgba(255,255,255,.10);
                border: 1px solid rgba(255,255,255,.18); border-radius: 24px;
            }
            .keypad-button:active { background: rgba(255,255,255,.28); }
            .submit-button {
                min-height: 42px; font-size: 16px; font-weight: 600;
                color: #000; background: #fff; border-radius: 10px;
            }
            .submit-button:disabled { opacity: .40; }
            .back-button { color: rgba(255,255,255,.70); background: transparent; }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


def main() -> int:
    service = os.environ.get("YUI_PAM_SERVICE", "phosh")
    try:
        authenticator = PamAuthenticator(service)
    except RuntimeError as error:
        print(f"locker: {error}", file=os.sys.stderr)
        return 1

    locker = LockScreen(authenticator)
    locker.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
