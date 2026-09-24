#!/usr/bin/env python3

import argparse
import os
import signal
import subprocess
import sys
import time


class Respawner:
    def __init__(self, command, delay=1.0):
        self.command = command
        self.delay = delay
        self.process = None
        self.stopping = False

    def signal_handler(self, signum, frame):
        self.stopping = True

        if self.process is not None and self.process.poll() is None:
            try:
                # Forward the signal to the entire child process group.
                os.killpg(self.process.pid, signum)
            except ProcessLookupError:
                pass

    def run(self):
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGHUP, self.signal_handler)

        while not self.stopping:
            try:
                self.process = subprocess.Popen(
                    self.command,
                    start_new_session=True
                )
            except FileNotFoundError:
                print(
                    f"lxrespawn.py: command not found: {self.command[0]}",
                    file=sys.stderr
                )
                return 127
            except OSError as exc:
                print(
                    f"lxrespawn.py: failed to execute {self.command[0]}: {exc}",
                    file=sys.stderr
                )
                return 126

            try:
                returncode = self.process.wait()
            except KeyboardInterrupt:
                self.signal_handler(signal.SIGINT, None)
                continue

            self.process = None

            if self.stopping:
                return returncode

            # Don't create a tight crash/respawn loop.
            if self.delay > 0:
                time.sleep(self.delay)

        return 0


def main():
    parser = argparse.ArgumentParser(
        prog="lxrespawn",
        description="Run a command and restart it whenever it exits."
    )

    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="delay before restarting the command (default: 1)"
    )

    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run"
    )

    args = parser.parse_args()

    if not args.command:
        parser.error("no command specified")

    # Allows:
    #   lxrespawn.py -- my-program --foo
    if args.command[0] == "--":
        args.command = args.command[1:]

    if not args.command:
        parser.error("no command specified")

    respawner = Respawner(args.command, max(0, args.delay))
    sys.exit(respawner.run())


if __name__ == "__main__":
    main()