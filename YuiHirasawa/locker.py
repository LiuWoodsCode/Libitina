#!/usr/bin/env python3
"""A small, touch-friendly Wayland lock screen for the Yui shell.
The password is checked against the current Unix user's password through PAM.
No password is stored by this program.  The PAM service defaults to
``login`` and can be overridden with the YUI_PAM_SERVICE environment variable.
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
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell
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
    """Minimal libpam client for authenticating the current user."""
    def __init__(self, service: str = "login") -> None:
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
    def authenticate(self, password: str) -> AuthenticationResult:
        if not password or "\0" in password:
            return AuthenticationResult(False, "Enter a password")
        secret = bytearray(password, "utf-8")
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
                return AuthenticationResult(False, "Incorrect password")
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
    MAX_PASSWORD_LENGTH = 0
    def __init__(self, authenticator: PamAuthenticator) -> None:
        super().__init__(title="Yui lock screen prototype")
        self._authenticator = authenticator
        self._authenticating = False
        self.set_decorated(False)
        self.set_keep_above(True)
        self.connect("delete-event", lambda *_args: True)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.RIGHT,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_namespace(self, "yui-locker")
        # Respect the keyboard's exclusive zone so it remains visible and usable.
        GtkLayerShell.set_exclusive_zone(self, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        self.add(box)
        self._clock_label = Gtk.Label()
        self._date_label = Gtk.Label()
        box.pack_start(self._clock_label, False, False, 0)
        box.pack_start(self._date_label, False, False, 0)
        box.pack_start(Gtk.Label(label="Enter password"), False, False, 8)
        self._entry = Gtk.Entry()
        self._entry.set_visibility(False)
        self._entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self._entry.set_max_length(self.MAX_PASSWORD_LENGTH)
        self._entry.set_placeholder_text("Password")
        self._entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.NO_EMOJI)
        self._entry.connect("changed", self._on_entry_changed)
        self._entry.connect("activate", self._submit)
        box.pack_start(self._entry, False, False, 0)
        keyboard = Gtk.Button(label="On-screen keyboard")
        keyboard.set_can_focus(False)
        keyboard.connect("clicked", self._show_keyboard)
        box.pack_start(keyboard, False, False, 0)
        self._submit_button = Gtk.Button(label="Unlock")
        self._submit_button.set_sensitive(False)
        self._submit_button.connect("clicked", self._submit)
        box.pack_start(self._submit_button, False, False, 0)
        self._status = Gtk.Label(label="")
        box.pack_start(self._status, False, False, 0)
        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)
    def _show_keyboard(self, _button: Gtk.Button) -> None:
        self._entry.grab_focus()
        Gio.bus_get(Gio.BusType.SESSION, None, self._keyboard_bus_ready)

    def _keyboard_bus_ready(self, _source, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
            bus.call(
                "sm.puri.OSK0", "/sm/puri/OSK0", "sm.puri.OSK0", "SetVisible",
                GLib.Variant("(b)", (True,)), None, Gio.DBusCallFlags.NONE,
                3000, None, self._keyboard_shown,
            )
        except GLib.Error:
            self._status.set_text("On-screen keyboard unavailable. Start squeekboard first.")

    def _keyboard_shown(self, bus, result) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error:
            self._status.set_text("On-screen keyboard unavailable. Start squeekboard first.")

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        if not self._authenticating:
            self._status.set_text("")
        self._submit_button.set_sensitive(
            bool(entry.get_text()) and not self._authenticating
        )
    def _submit(self, _widget: Gtk.Widget) -> None:
        if self._authenticating:
            return
        password = self._entry.get_text()
        if not password:
            return
        self._authenticating = True
        self._entry.set_sensitive(False)
        self._submit_button.set_sensitive(False)
        self._status.set_text("Checking...")
        threading.Thread(
            target=self._authenticate_worker,
            args=(password,),
            name="YuiPamAuthentication",
            daemon=True,
        ).start()
    def _authenticate_worker(self, password: str) -> None:
        try:
            result = self._authenticator.authenticate(password)
        except Exception as error:
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
        self._entry.grab_focus()
        return GLib.SOURCE_REMOVE
    def _update_clock(self) -> bool:
        now = dt.datetime.now().astimezone()
        self._clock_label.set_text(now.strftime("%H:%M"))
        self._date_label.set_text(now.strftime("%A, %B %-d"))
        return GLib.SOURCE_CONTINUE
def main() -> int:
    service = os.environ.get("YUI_PAM_SERVICE", "login")
    try:
        authenticator = PamAuthenticator(service)
    except RuntimeError as error:
        print(f"locker: {error}", file=os.sys.stderr)
        return 1
    locker = LockScreen(authenticator)
    locker.show_all()
    locker._entry.grab_focus()
    Gtk.main()
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
