"""Check password handling without a Wayland display or real PAM login."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class PasswordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gi = ModuleType("gi")
        gi.require_version = Mock()
        repository = ModuleType("gi.repository")
        for name in ("Gdk", "Gio", "GLib", "GtkLayerShell"):
            setattr(repository, name, Mock())
        repository.Gtk = SimpleNamespace(Window=object)
        spec = importlib.util.spec_from_file_location(
            "locker_password_test_module", Path(__file__).resolve().parents[1] / "locker.py"
        )
        cls.locker = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository,
                                      spec.name: cls.locker}):
            spec.loader.exec_module(cls.locker)

    def test_mixed_passwords_reach_pam_and_secret_buffer_is_cleared(self):
        for password in ("Abc123!", " spaces allowed ", "päss日本語", "x" * 100):
            with self.subTest(password=password):
                auth = object.__new__(self.locker.PamAuthenticator)
                auth._pam = Mock()
                auth._pam.pam_start.return_value = self.locker.PAM_SUCCESS
                auth._pam.pam_authenticate.return_value = self.locker.PAM_SUCCESS
                auth._pam.pam_acct_mgmt.return_value = self.locker.PAM_SUCCESS
                auth._service = b"login"
                auth._username = b"test"
                buffers = []

                def conversation(secret):
                    self.assertEqual(bytes(secret), password.encode("utf-8"))
                    buffers.append(secret)
                    return self.locker.PamConversationFunction(lambda *_args: 0)

                auth._make_conversation = conversation
                self.assertTrue(auth.authenticate(password).accepted)
                auth._pam.pam_authenticate.assert_called_once()
                self.assertEqual(bytes(buffers[0]), bytes(len(password.encode("utf-8"))))

    def test_empty_and_nul_passwords_do_not_reach_pam(self):
        auth = object.__new__(self.locker.PamAuthenticator)
        auth._pam = Mock()
        for password in ("", "abc\0def"):
            self.assertFalse(auth.authenticate(password).accepted)
        auth._pam.pam_start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
