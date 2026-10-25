#!/bin/sh
# Install Yui on postmarketOS, then use the shared package-free setup.
set -eu

for argument in "$@"; do
    case ${argument} in
        --force-legacy) ;;
        -h|--help)
            echo "Usage: $0 [--force-legacy]"
            echo "Install postmarketOS dependencies and the YuiHirasawa login session."
            echo "  --force-legacy  Also install Yui in the user's Labwc autostart."
            exit 0
            ;;
        *)
            echo "error: unknown argument: ${argument}" >&2
            exit 1
            ;;
    esac
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
for program in install-generic.sh install-session.py launcher.py main.py notification.py; do
    if [ ! -f "${script_dir}/${program}" ]; then
        echo "error: required Yui program not found: ${script_dir}/${program}" >&2
        exit 1
    fi
done

if [ "$(id -u)" -eq 0 ]; then
    privilege=''
elif command -v doas >/dev/null 2>&1; then
    privilege=doas
elif command -v sudo >/dev/null 2>&1; then
    privilege=sudo
else
    echo "error: run as root, or install doas/sudo first" >&2
    exit 1
fi

echo "Installing Labwc and Yui dependencies from configured repositories..."
# Deliberately do not change repositories, upgrade the OS, or enable services.
${privilege:+"${privilege}"} apk add --no-cache \
    bash bluez brightnessctl dbus dbus-x11 foot \
    gtk+3.0 gtk-layer-shell libhandy1 gobject-introspection \
    gstreamer gst-plugins-base gst-plugins-good \
    labwc networkmanager pipewire pipewire-tools upower wireplumber wlr-randr \
    linux-pam python3 py3-dbus py3-dbus-next py3-gobject3 py3-cairo \
    py3-wayland squeekboard

# Keep the invoking user's environment and identity for optional autostart setup.
exec bash "${script_dir}/install-generic.sh" "$@"
