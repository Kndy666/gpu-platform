#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' 'Run bootstrap as root.' >&2
    exit 1
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
venv=/opt/gpu-platform/installer
apt-get update
apt-get install -y python3 python3-venv
python3 -m venv "$venv"
set -- "$root/packages/contracts" "$root/packages/installer"
for package in "$root"/modules/*/*; do
    if [ -f "$package/pyproject.toml" ]; then
        set -- "$@" "$package"
    fi
done
"$venv/bin/pip" install "$@"
ln -sfn "$venv/bin/platformctl" /usr/local/bin/platformctl
printf '%s\n' 'Run platformctl install --config <cluster.yaml> --host <host-name>.'
