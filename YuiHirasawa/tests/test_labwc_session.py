"""Verify temporary mobile configuration and session cleanup without a compositor."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import labwc_session


class SessionTests(unittest.TestCase):
    def check_session(self, shell):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            user = base / "user"
            system = base / "system"
            for directory in (user, system):
                (directory / "labwc").mkdir(parents=True)
            original = b'<labwc_config><theme><name>My theme</name></theme><windowRules><windowRule identifier="special" serverDecoration="yes" /></windowRules></labwc_config>'
            source = user / "labwc" / "rc.xml"
            source.write_bytes(original)
            (system / "labwc" / "rc.xml").write_text('<labwc_config><core><gap>4</gap></core></labwc_config>')
            paths = []

            def run(command, env):
                overlay = Path(env["XDG_CONFIG_DIRS"].split(":")[0])
                paths.append(overlay)
                self.assertTrue((overlay / "labwc" / "rc.xml").is_file())
                if shell == "mobile":
                    config = Path(command[command.index("--config") + 1])
                    paths.append(config)
                    root = ET.parse(config).getroot()
                    self.assertEqual(root.findtext("theme/name"), "My theme")
                    self.assertEqual(root.findtext("core/gap"), "4")
                    self.assertEqual(len(root.findall("keyboard/keybind")), 2)
                    rules = root.findall("windowRules/windowRule")
                    self.assertEqual(rules[0].get("identifier"), "special")
                    self.assertEqual(rules[-1].attrib, {"identifier": "*", "serverDecoration": "no"})
                    self.assertEqual(rules[-1].find("action").get("name"), "Maximize")
                else:
                    self.assertNotIn("--config", command)
                self.assertEqual(source.read_bytes(), original)
                return type("Result", (), {"returncode": 0})()

            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(user), "XDG_CONFIG_DIRS": str(system)}), patch.object(labwc_session, "load_config", return_value={"shell": shell}), patch.object(labwc_session.subprocess, "run", side_effect=run):
                self.assertEqual(labwc_session.main(), 0)
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(all(not path.exists() for path in paths))

    def test_mobile_copy_preserves_settings_and_is_cleaned_up(self):
        self.check_session("mobile")

    def test_desktop_keeps_existing_config_selection(self):
        self.check_session("desktop")

    def test_mobile_without_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            dirs = [Path(temporary)]
            root = ET.fromstring(labwc_session.mobile_xml(dirs, labwc_session.overlay_xml(dirs)))
            self.assertIsNotNone(root.find("keyboard/default"))
            self.assertEqual(root.find("windowRules/windowRule/action").get("name"), "Maximize")


if __name__ == "__main__":
    unittest.main()
