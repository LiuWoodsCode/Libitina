#!/usr/bin/env bash
# Install Yui's Debian dependencies and start Yui with this user's Labwc session.

set -euo pipefail

if [[ ! -r /etc/debian_version ]]; then
    echo "error: this installer is intended for Debian" >&2
    exit 1
fi

if [[ ${EUID} -eq 0 && -n ${SUDO_USER:-} && ${SUDO_USER} != root ]]; then
    target_user=${SUDO_USER}
else
    target_user=$(id -un)
fi

if ! target_entry=$(getent passwd "${target_user}"); then
    echo "error: could not find user '${target_user}'" >&2
    exit 1
fi

IFS=: read -r _ _ target_uid target_gid _ target_home _ <<<"${target_entry}"

if [[ -n ${XDG_CONFIG_HOME:-} ]]; then
    if [[ ${XDG_CONFIG_HOME} != /* ]]; then
        echo "error: XDG_CONFIG_HOME must be an absolute path" >&2
        exit 1
    fi
    config_home=${XDG_CONFIG_HOME}
else
    config_home=${target_home}/.config
fi

if [[ ${EUID} -eq 0 ]]; then
    apt=(apt-get)
else
    if ! command -v sudo >/dev/null 2>&1; then
        echo "error: sudo is required when this installer is not run as root" >&2
        exit 1
    fi
    apt=(sudo apt-get)
fi

packages=(
    dbus-user-session
    gir1.2-gstreamer-1.0
    gir1.2-gtk-3.0
    gir1.2-gtklayershell-0.1
    gstreamer1.0-plugins-base
    gstreamer1.0-plugins-good
    labwc
    libpam0g
    python3
    python3-dbus
    python3-dbus-next
    python3-gi
    python3-pywayland
)

echo "Installing Labwc and Yui dependencies..."
"${apt[@]}" update
"${apt[@]}" install -y "${packages[@]}"

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
main_path=${script_dir}/main.py
notification_path=${script_dir}/notification.py

for path in "${main_path}" "${notification_path}"; do
    if [[ ! -f ${path} ]]; then
        echo "error: required Yui program not found: ${path}" >&2
        exit 1
    fi
done

labwc_dir=${config_home}/labwc
autostart_path=${labwc_dir}/autostart

if [[ ${EUID} -eq 0 ]]; then
    install -d -m 700 -o "${target_uid}" -g "${target_gid}" "${labwc_dir}"
else
    install -d -m 700 "${labwc_dir}"
fi

begin_marker="# BEGIN Yui shell (managed by install-debian.sh)"
end_marker="# END Yui shell (managed by install-debian.sh)"
legacy_begin_marker="# BEGIN Yui shell (managed by Misc/setup.py)"
legacy_end_marker="# END Yui shell (managed by Misc/setup.py)"
temporary_path=$(mktemp "${labwc_dir}/.autostart.XXXXXX")
trap 'rm -f -- "${temporary_path}"' EXIT

if [[ -f ${autostart_path} ]]; then
    awk \
        -v begin="${begin_marker}" \
        -v end="${end_marker}" \
        -v legacy_begin="${legacy_begin_marker}" \
        -v legacy_end="${legacy_end_marker}" '
        $0 == begin || $0 == legacy_begin { managed = 1; next }
        $0 == end || $0 == legacy_end { managed = 0; next }
        !managed { print }
    ' "${autostart_path}" >"${temporary_path}"
else
    printf '#!/bin/sh\n' >"${temporary_path}"
fi

# Bash's %q produces shell-safe paths for Labwc's POSIX-shell autostart file.
printf -v main_command '/usr/bin/python3 %q &' "${main_path}"
printf -v notification_command '/usr/bin/python3 %q &' "${notification_path}"

if [[ -s ${temporary_path} ]] && [[ $(tail -c 1 "${temporary_path}" | wc -l) -eq 0 ]]; then
    printf '\n' >>"${temporary_path}"
fi
if [[ -s ${temporary_path} ]] && [[ -n $(tail -n 1 "${temporary_path}") ]]; then
    printf '\n' >>"${temporary_path}"
fi

cat >>"${temporary_path}" <<EOF
${begin_marker}
${main_command}
${notification_command}
${end_marker}
EOF

if [[ -e ${autostart_path} ]]; then
    chmod --reference="${autostart_path}" "${temporary_path}"
else
    chmod 700 "${temporary_path}"
fi

if [[ ${EUID} -eq 0 ]]; then
    chown "${target_uid}:${target_gid}" "${temporary_path}"
fi

mv -f -- "${temporary_path}" "${autostart_path}"
trap - EXIT

echo "Configured ${autostart_path} for ${target_user}."
echo "Yui will start the next time Labwc launches."
