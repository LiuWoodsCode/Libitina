#!/usr/bin/env bash
# Register Yui's login session without installing any packages.

set -euo pipefail

force_legacy=false
for argument in "$@"; do
    case ${argument} in
        --force-legacy)
            force_legacy=true
            ;;
        -h|--help)
            echo "Usage: $0 [--force-legacy]"
            echo "Install the YuiHirasawa login session; no packages are installed."
            echo "Requires Bash, /usr/bin/python3, and Yui runtime dependencies."
            echo "  --force-legacy  Also install Yui in the user's Labwc autostart."
            exit 0
            ;;
        *)
            echo "error: unknown argument: ${argument}" >&2
            echo "Usage: $0 [--force-legacy]" >&2
            exit 1
            ;;
    esac
done

if [[ ${EUID} -eq 0 && -n ${SUDO_USER:-} && ${SUDO_USER} != root ]]; then
    target_user=${SUDO_USER}
elif [[ ${EUID} -eq 0 && -n ${DOAS_USER:-} && ${DOAS_USER} != root ]]; then
    target_user=${DOAS_USER}
else
    target_user=$(id -un)
fi

if [[ ! -x /usr/bin/python3 ]]; then
    echo "error: install Python 3 at /usr/bin/python3 before running this installer" >&2
    exit 1
fi

# Python's pwd module works on musl systems without a getent command.
target_entry=$(/usr/bin/python3 - "${target_user}" <<'PYTHON'
import pwd
import sys
entry = pwd.getpwnam(sys.argv[1])
print(f"{entry.pw_uid}:{entry.pw_gid}:{entry.pw_dir}")
PYTHON
)
IFS=: read -r target_uid target_gid target_home <<<"${target_entry}"

if [[ -n ${XDG_CONFIG_HOME:-} ]]; then
    if [[ ${XDG_CONFIG_HOME} != /* ]]; then
        echo "error: XDG_CONFIG_HOME must be an absolute path" >&2
        exit 1
    fi
    config_home=${XDG_CONFIG_HOME}
else
    config_home=${target_home}/.config
fi

# Elevate only operations that need system-wide write access.
privilege=()
if [[ ${EUID} -ne 0 ]]; then
    if command -v doas >/dev/null 2>&1; then
        privilege=(doas)
    elif command -v sudo >/dev/null 2>&1; then
        privilege=(sudo)
    else
        echo "error: run as root, or install sudo/doas to register the login session" >&2
        exit 1
    fi
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
launcher_path=${script_dir}/launcher.py
main_path=${script_dir}/main.py
notification_path=${script_dir}/notification.py

for path in "${launcher_path}" "${main_path}" "${notification_path}"; do
    if [[ ! -f ${path} ]]; then
        echo "error: required Yui program not found: ${path}" >&2
        exit 1
    fi
done

if [[ ${force_legacy} == true ]]; then
    labwc_dir=${config_home}/labwc
    autostart_path=${labwc_dir}/autostart

    if [[ ${EUID} -eq 0 ]]; then
        install -d -m 700 -o "${target_uid}" -g "${target_gid}" "${labwc_dir}"
    else
        install -d -m 700 "${labwc_dir}"
    fi

    # Share markers with the Debian installer for idempotent migration.
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

    # Labwc runs autostart with /bin/sh, so use POSIX quoting (not Bash %q).
    launcher_command=$(/usr/bin/python3 - "${launcher_path}" <<'PYTHON'
import shlex
import sys
print(shlex.join(["/usr/bin/python3", sys.argv[1]]) + " &")
PYTHON
)

    if [[ -s ${temporary_path} ]] && [[ $(tail -c 1 "${temporary_path}" | wc -l) -eq 0 ]]; then
        printf '\n' >>"${temporary_path}"
    fi
    if [[ -s ${temporary_path} ]] && [[ -n $(tail -n 1 "${temporary_path}") ]]; then
        printf '\n' >>"${temporary_path}"
    fi

    cat >>"${temporary_path}" <<EOF
${begin_marker}
${launcher_command}
${end_marker}
EOF

    if [[ -e ${autostart_path} ]]; then
        /usr/bin/python3 - "${autostart_path}" "${temporary_path}" <<'PYTHON'
import os
import stat
import sys
os.chmod(sys.argv[2], stat.S_IMODE(os.stat(sys.argv[1]).st_mode))
PYTHON
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
else
    echo "Skipping legacy Labwc autostart (enable with --force-legacy)."
fi

# Register an additional login option; retain the existing Labwc session.
"${privilege[@]}" /usr/bin/python3 "${script_dir}/install-session.py"
