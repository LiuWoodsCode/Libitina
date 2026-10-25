"""Exercise installers in an isolated tree without packages or system writes."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / "Yui user's $shell"
        self.source.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.environment = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                            "XDG_CONFIG_HOME": str(self.base / "config")}
        for name in ("launcher.py", "main.py", "notification.py"):
            (self.source / name).touch()
        (self.source / "install-session.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(self.base / 'session-installed')!r}).touch()\n"
        )
        # Resolve Python on the test host; installed sessions use /usr/bin/python3.
        script = (ROOT / "install-generic.sh").read_text().replace("/usr/bin/python3", sys.executable)
        (self.source / "install-generic.sh").write_text(script)
        for name in ("doas", "sudo"):
            path = self.bin / name
            path.write_text('#!/bin/sh\nexec "$@"\n')
            path.chmod(0o755)

    def run_script(self, shell, path, *arguments):
        return subprocess.run([shell, str(path), *arguments], env=self.environment,
                              text=True, capture_output=True)

    def test_generic_preserves_autostart_and_replaces_managed_block(self):
        autostart = Path(self.environment["XDG_CONFIG_HOME"]) / "labwc/autostart"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("#!/bin/sh\necho keep-me\n")
        autostart.chmod(0o750)
        installer = self.source / "install-generic.sh"
        for _ in range(2):
            result = self.run_script("bash", installer, "--force-legacy")
            self.assertEqual(result.returncode, 0, result.stderr)
        contents = autostart.read_text()
        self.assertIn("echo keep-me", contents)
        self.assertEqual(contents.count("# BEGIN Yui shell"), 1)
        self.assertEqual(autostart.stat().st_mode & 0o777, 0o750)
        command = next(line for line in contents.splitlines() if line.endswith(" &"))
        self.assertEqual(shlex.split(command)[:-1], [sys.executable, str((self.source / "launcher.py").resolve())])
        self.assertTrue((self.base / "session-installed").exists())

    def test_generic_default_does_not_create_autostart(self):
        result = self.run_script("bash", self.source / "install-generic.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(Path(self.environment["XDG_CONFIG_HOME"]).exists())
        self.assertTrue((self.base / "session-installed").exists())

    def test_postmarketos_installs_packages_then_delegates(self):
        release = self.base / "postmarketos-release"
        release.touch()
        script = (ROOT / "install-postmarketos.sh").read_text().replace(
            "/etc/postmarketos-release", str(release))
        installer = self.source / "install-postmarketos.sh"
        installer.write_text(script)
        apk = self.bin / "apk"
        apk.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {shlex.quote(str(self.base / 'apk-args'))}\n")
        apk.chmod(0o755)
        result = self.run_script("sh", installer, "--force-legacy")
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = (self.base / "apk-args").read_text().splitlines()
        self.assertEqual(packages[:2], ["add", "--no-cache"])
        self.assertIn("py3-wayland", packages)
        self.assertIn("squeekboard", packages)
        self.assertTrue((self.base / "session-installed").exists())

    def test_help_and_unknown_options_have_no_side_effects(self):
        for name, shell in (("install-generic.sh", "bash"), ("install-postmarketos.sh", "sh")):
            for option, expected in (("--help", 0), ("--unknown", 1)):
                result = self.run_script(shell, ROOT / name, option)
                self.assertEqual(result.returncode, expected, result.stderr)
        self.assertFalse((self.base / "session-installed").exists())


if __name__ == "__main__":
    unittest.main()
