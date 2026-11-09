#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! python3 -c 'import yaml' 2>/dev/null; then
  if [[ "${EUID}" != 0 ]]; then
    echo 'Install python3-yaml or run this installer with sudo.' >&2; exit 1
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-yaml ca-certificates curl
fi
exec python3 "${project_root}/deploy/install.py" "$@"
