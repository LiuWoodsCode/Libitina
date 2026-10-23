"""wlr-randr display profiles shared by Settings and the session launcher."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

CONFIG_DIR = Path.home() / ".config" / "yui"
CONFIG_PATH = CONFIG_DIR / "monitors.json"
STATUS_PATH = CONFIG_DIR / "monitors-status.json"
TRANSFORMS = ("normal", "90", "180", "270", "flipped", "flipped-90",
              "flipped-180", "flipped-270")
MODE_PATTERN = re.compile(r"[1-9][0-9]*x[1-9][0-9]*(?:@([0-9]+(?:\.[0-9]+)?)Hz)?")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_randr(arguments):
    try:
        result = subprocess.run(["wlr-randr", *arguments], capture_output=True,
                                text=True, timeout=10, check=False)
    except FileNotFoundError as exc:
        raise ValueError("Install wlr-randr to manage displays.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("wlr-randr timed out.") from exc
    if result.returncode:
        raise ValueError(result.stderr.strip() or "wlr-randr failed.")
    return result.stdout


def query_outputs():
    try:
        outputs = json.loads(run_randr(["--json"]))
    except json.JSONDecodeError as exc:
        raise ValueError("wlr-randr returned invalid JSON. Install a version with --json support.") from exc
    if not isinstance(outputs, list) or any(
        not isinstance(output, dict) or not isinstance(output.get("name"), str)
        for output in outputs
    ):
        raise ValueError("Unexpected wlr-randr output format.")
    return outputs


def mode_name(mode):
    value = f'{mode["width"]}x{mode["height"]}'
    if mode.get("refresh"):
        value += f'@{mode["refresh"]:g}Hz'
    return value


def snapshot(outputs):
    profiles = []
    for output in outputs:
        current = next((mode for mode in output.get("modes", []) if mode.get("current")), None)
        profiles.append({
            "name": output["name"], "enabled": output["enabled"],
            "mode": mode_name(current) if current else "",
            "custom_mode": False, "position": output.get("position", {"x": 0, "y": 0}),
            "scale": output.get("scale", 1), "transform": output.get("transform", "normal"),
            "adaptive_sync": output.get("adaptive_sync"),
        })
    return profiles


def validate(profiles):
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("No monitor configuration to save.")
    names = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("Invalid monitor configuration.")
        name = profile.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Monitor names must be unique and nonempty.")
        names.add(name)
        if type(profile.get("enabled")) is not bool:
            raise ValueError("Enabled must be a boolean.")
        mode = profile.get("mode", "")
        match = MODE_PATTERN.fullmatch(mode) if isinstance(mode, str) else None
        if not isinstance(mode, str) or (mode and not match):
            raise ValueError("Use a mode such as 1920x1080@60Hz.")
        if match and match.group(1) is not None:
            refresh = float(match.group(1))
            if not math.isfinite(refresh) or refresh <= 0:
                raise ValueError("Refresh rate must be a positive number.")
        if type(profile.get("custom_mode", False)) is not bool:
            raise ValueError("Custom mode must be a boolean.")
        if profile.get("custom_mode") and not mode:
            raise ValueError("Enter a resolution for the custom mode.")
        scale = profile.get("scale", 1)
        if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
            raise ValueError("Scale must be a positive number.")
        if profile.get("transform", "normal") not in TRANSFORMS:
            raise ValueError("Unknown display transform.")
        position = profile.get("position", {})
        if not isinstance(position, dict) or any(type(position.get(axis)) is not int
                                                 for axis in ("x", "y")):
            raise ValueError("Position must contain integer X and Y coordinates.")
        if profile.get("adaptive_sync") is not None and type(profile["adaptive_sync"]) is not bool:
            raise ValueError("Adaptive sync must be a boolean or null.")
    if not any(profile["enabled"] for profile in profiles):
        raise ValueError("Keep at least one display enabled.")
    return profiles


def load_profiles():
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported monitor configuration version.")
    return validate(data.get("outputs"))


def save_profiles(profiles):
    write_json(CONFIG_PATH, {"version": 1, "outputs": validate(profiles)})


def build_arguments(profiles, outputs):
    validate(profiles)
    connected = {output["name"]: output for output in outputs}
    selected = {profile["name"]: profile for profile in profiles if profile["name"] in connected}
    if not selected:
        return []
    if not any(selected.get(name, output)["enabled"] for name, output in connected.items()):
        raise ValueError("Saved settings would disable every connected display.")
    arguments = []
    for name, profile in selected.items():
        arguments += ["--output", name, "--on" if profile["enabled"] else "--off"]
        if not profile["enabled"]:
            continue
        if profile.get("mode"):
            arguments += ["--custom-mode" if profile.get("custom_mode") else "--mode", profile["mode"]]
        position = profile["position"]
        arguments += ["--pos", f'{position["x"]},{position["y"]}',
                      "--scale", str(profile.get("scale", 1)),
                      "--transform", profile.get("transform", "normal")]
        if profile.get("adaptive_sync") is not None and connected[name].get("adaptive_sync") is not None:
            arguments += ["--adaptive-sync", "enabled" if profile["adaptive_sync"] else "disabled"]
    return arguments


def watch():
    """Apply on startup, profile edits, and hotplug; never restart the shell."""
    previous = None
    while True:
        revision = None
        try:
            if CONFIG_PATH.exists():
                revision = CONFIG_PATH.stat().st_mtime_ns
                outputs = query_outputs()
                key = (revision, tuple(sorted(output["name"] for output in outputs)))
                if key != previous:
                    previous = key
                    arguments = build_arguments(load_profiles(), outputs)
                    if arguments:
                        run_randr(["--dryrun", *arguments])
                        run_randr(arguments)
                    write_json(STATUS_PATH, {"revision": revision, "ok": True,
                                            "message": "Display settings applied." if arguments else
                                            "Saved monitors are not connected."})
            else:
                previous = None
        except (OSError, ValueError) as exc:
            # A failed request is retried after an edit or hotplug, not every poll.
            status = {"revision": revision, "ok": False, "message": str(exc)}
            try:
                if not STATUS_PATH.exists() or json.loads(STATUS_PATH.read_text()) != status:
                    write_json(STATUS_PATH, status)
            except (OSError, ValueError):
                pass
        time.sleep(2)


if __name__ == "__main__":
    watch()
