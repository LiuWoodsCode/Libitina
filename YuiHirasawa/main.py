"""Launch the configured Yui shell."""

from importlib import import_module
import sys

from settings import load_config


def main() -> int:
    if sys.argv[1:] == ["--show-launcher"]:
        from launcher_control import open_launcher
        return open_launcher()
    shell = import_module(load_config()["shell"])
    return shell.main()


if __name__ == "__main__":
    raise SystemExit(main())
