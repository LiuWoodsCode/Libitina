#!/usr/bin/env python3
"""Run Labwc with temporary Yui launcher bindings; preserve user settings."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from settings import load_config


def mobile_xml(config_dirs, bindings):
    """Copy the merged session settings and append mobile window defaults."""
    root = ET.Element("labwc_config")
    # Match the desktop session's order: system files, Yui, then user settings.
    for directory in reversed(config_dirs[1:]):
        path = directory / "labwc" / "rc.xml"
        if path.is_file():
            root.extend(ET.parse(path).getroot())
    root.extend(ET.fromstring(bindings))
    user_path = config_dirs[0] / "labwc" / "rc.xml"
    if user_path.is_file():
        root.extend(ET.parse(user_path).getroot())
    rules = ET.SubElement(root, "windowRules")
    rule = ET.SubElement(rules, "windowRule", identifier="*", serverDecoration="no")
    ET.SubElement(rule, "action", name="Maximize")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def overlay_xml(config_dirs):
    root = ET.Element("labwc_config")
    keyboard = ET.SubElement(root, "keyboard")
    has_bindings = False
    for directory in config_dirs:
        path = directory / "labwc" / "rc.xml"
        if path.is_file():
            has_bindings |= any(node.tag.split("}")[-1] == "keybind"
                                for node in ET.parse(path).getroot().iter())
    if not has_bindings:
        ET.SubElement(keyboard, "default")
    command = shlex.join([sys.executable, str(Path(__file__).resolve().with_name("main.py")),
                          "--show-launcher"])
    for key in ("Super_L", "Super_R"):
        binding = ET.SubElement(keyboard, "keybind", key=key, onRelease="yes")
        ET.SubElement(binding, "action", name="Execute", command=command)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main():
    environment = os.environ.copy()
    system_dirs = environment.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    user_dir = Path(environment.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    config_dirs = [user_dir, *(Path(p) for p in system_dirs.split(":") if p)]
    try:
        xml = overlay_xml(config_dirs)
        mobile = load_config()["shell"] == "mobile"
        with tempfile.TemporaryDirectory(prefix="yui-labwc-") as temporary:
            directory = Path(temporary) / "labwc"
            directory.mkdir()
            (directory / "rc.xml").write_bytes(xml)
            # System configuration, then Yui bindings, then user overrides.
            environment["XDG_CONFIG_DIRS"] = temporary + ":" + system_dirs
            startup = shlex.join([sys.executable, str(Path(__file__).resolve().with_name("launcher.py"))])
            command = ["labwc", "--merge-config", "--startup", startup]
            if mobile:
                mobile_path = Path(temporary) / "mobile-rc.xml"
                mobile_path.write_bytes(mobile_xml(config_dirs, xml))
                command.extend(["--config", str(mobile_path)])
            return subprocess.run(command,
                                  env=environment).returncode
    except (OSError, ET.ParseError) as exc:
        print(f"Could not start YuiHirasawa: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
