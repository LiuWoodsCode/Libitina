"""Verify journal wrapping preserves launcher arguments and process ownership."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher


class JournalTests(unittest.TestCase):
    def test_worker_starts_keyboard_only_when_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), \
                    patch.object(sys, "argv", ["launcher.py", "--worker"]), \
                    patch.object(launcher, "connect_journal"), \
                    patch.object(launcher, "load_config", return_value={"squeekboard_enabled": enabled}), \
                    patch.object(launcher, "supervise", return_value=0) as supervise:
                self.assertEqual(launcher.main(), 0)
                commands = supervise.call_args.args[0]
                self.assertEqual("squeekboard" in commands, enabled)
                if enabled:
                    self.assertEqual(commands["squeekboard"], ["squeekboard"])
                self.assertIn("main.py", commands)

    def test_wraps_session_and_worker_without_extra_supervisor(self):
        for arguments in ([], ["--session"], ["--worker"]):
            with self.subTest(arguments=arguments), patch.dict(os.environ, {}, clear=True), \
                    patch.object(sys, "argv", ["launcher.py", *arguments]), \
                    patch.object(launcher.shutil, "which", return_value="/usr/bin/systemd-cat"), \
                    patch.object(Path, "is_socket", return_value=True), \
                    patch.object(launcher.os, "execve") as execute:
                launcher.connect_journal()
                executable, command, environment = execute.call_args.args
                self.assertEqual(executable, "/usr/bin/systemd-cat")
                self.assertEqual(command[1:6], ["--identifier=yui", "--priority=info",
                                               "--stderr-priority=err", "--level-prefix=no", "--"])
                self.assertEqual(command[6:], [sys.executable, "-u", str(Path(launcher.__file__).resolve()), *arguments])
                self.assertEqual(environment["PYTHONUNBUFFERED"], "1")
                self.assertEqual(environment["YUI_JOURNAL_CONNECTED"], "1")

    def test_descendants_do_not_wrap_again(self):
        with patch.dict(os.environ, {"YUI_JOURNAL_CONNECTED": "1"}), \
                patch.object(launcher.shutil, "which") as which:
            launcher.connect_journal()
            which.assert_not_called()

    def test_unavailable_journal_or_exec_failure_retains_output(self):
        for binary, socket, failure in ((None, True, False), ("/bin/systemd-cat", False, False),
                                        ("/bin/systemd-cat", True, True)):
            with self.subTest(binary=binary, socket=socket), patch.dict(os.environ, {}, clear=True), \
                    patch.object(launcher.shutil, "which", return_value=binary), \
                    patch.object(Path, "is_socket", return_value=socket), \
                    patch.object(launcher.os, "execve", side_effect=OSError("unavailable")) as execute, \
                    patch.object(sys, "stderr") as stderr:
                launcher.connect_journal()
                self.assertEqual(execute.call_count, int(failure))
                self.assertTrue(stderr.write.called)
                self.assertNotIn("YUI_JOURNAL_CONNECTED", os.environ)


if __name__ == "__main__":
    unittest.main()
