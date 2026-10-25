#!/usr/bin/env python3
"""Supervise the Yui shell and notification daemon for a Wayland session."""

import argparse
import fcntl
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

from settings import load_config

ROOT = Path(__file__).resolve().parent
RESTART_DELAY = 1.0


def connect_journal():
    """Re-exec through systemd-cat so all descendants inherit journal streams."""
    if os.environ.get("YUI_JOURNAL_CONNECTED") == "1":
        return
    journal_cat = shutil.which("systemd-cat")
    if journal_cat is None or not Path("/run/systemd/journal/stdout").is_socket():
        print("Yui: journal unavailable; keeping stdout/stderr", file=sys.stderr, flush=True)
        return
    environment = os.environ.copy()
    environment["YUI_JOURNAL_CONNECTED"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    command = [journal_cat, "--identifier=yui", "--priority=info",
               "--stderr-priority=err", "--level-prefix=no", "--",
               sys.executable, "-u", str(Path(__file__).resolve()), *sys.argv[1:]]
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execve(journal_cat, command, environment)
    except OSError as exc:
        print(f"Yui: could not connect to journal: {exc}; keeping stdout/stderr",
              file=sys.stderr, flush=True)


def terminate_group(child, sig=signal.SIGKILL):
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


def supervise(commands):
    """Restart exited children, and reap every child when the session stops."""
    stopping = False
    children = {}
    retry_at = {}

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        while not stopping:
            for name, command in commands.items():
                child = children.get(name)
                if child is not None:
                    status = child.poll()
                    if status is None:
                        continue
                    print(f"Yui: {name} exited ({status}); restarting", file=sys.stderr, flush=True)
                    if name == "launcher":
                        terminate_group(child)
                    del children[name]
                    retry_at[name] = time.monotonic() + RESTART_DELAY
                if time.monotonic() >= retry_at.get(name, 0):
                    try:
                        children[name] = subprocess.Popen(command, cwd=ROOT, start_new_session=(name == "launcher"))
                        print(f"Yui: started {name} (PID {children[name].pid})", file=sys.stderr, flush=True)
                    except OSError as exc:
                        print(f"Yui: could not start {name}: {exc}", file=sys.stderr, flush=True)
                        retry_at[name] = time.monotonic() + RESTART_DELAY
            time.sleep(0.1)
    finally:
        for name, child in children.items():
            if name == "launcher":
                terminate_group(child, signal.SIGTERM)
            elif child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 7
        for child in children.values():
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                if "launcher" in commands:
                    terminate_group(child)
                else:
                    child.kill()
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", action="store_true", help="start the Labwc login session")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    connect_journal()
    if args.session:
        from labwc_session import main as run_session
        return run_session()
    if args.worker:
        commands = {name: [sys.executable, str(ROOT / name)]
                    for name in ("notification.py", "main.py", "displays.py")}
        if load_config()["squeekboard_enabled"]:
            commands["squeekboard"] = ["squeekboard"]
        return supervise(commands)

    # Hold the lock in the supervisor so a restarted worker cannot race autostart.
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/yui-{os.getuid()}")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / "yui-launcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return supervise({"launcher": [sys.executable, str(Path(__file__).resolve()), "--worker"]})


if __name__ == "__main__":
    raise SystemExit(main())
