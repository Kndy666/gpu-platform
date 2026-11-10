#!/usr/bin/env bash
# Controller or worker on Ubuntu; existing data filesystems are never formatted.
set -euo pipefail
[[ "${EUID}" == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config_path="${1:-${project_root}/config.yaml}"
[[ -f "${config_path}" ]] || { echo 'Copy config.example.yaml to config.yaml and set the server address and mounted data disks.' >&2; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && ( "$VERSION_ID" == 22.04 || "$VERSION_ID" == 24.04 ) && ( "$(uname -m)" == aarch64 || "$(uname -m)" == x86_64 ) ]] || { echo 'Deployment requires Ubuntu 22.04/24.04 ARM64 or AMD64.' >&2; exit 1; }
command -v nvidia-smi >/dev/null || { echo 'Install NVIDIA R620 (620.70.06 verified) and reboot before deployment.' >&2; exit 1; }
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
nvidia-smi --query-gpu=driver_version --format=csv,noheader | while IFS= read -r driver_version; do
  dpkg --compare-versions "$driver_version" ge 620.70.06 || { echo 'Upgrade every GPU to NVIDIA R620 (620.70.06 or newer) before deployment.' >&2; exit 1; }
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-yaml python3-venv ca-certificates curl git \
  docker.io iproute2 iptables util-linux openssl gnupg apparmor apparmor-utils openssh-client python3-requests
systemctl enable --now docker
"${project_root}/deploy/install.sh" --config "${config_path}" --preflight
role="$(python3 - "${config_path}" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['platform'].get('role', 'controller'))
PY
)"
if [[ "${role}" == controller ]]; then
image="$(python3 - "${config_path}" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['platform']['app_image'])
PY
)"
docker build --network=host \
  --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12.12-slim-bookworm}" \
  -t "${image}" "${project_root}"
fi
"${project_root}/deploy/prepare.sh"
"${project_root}/deploy/install.sh" --config "${config_path}"
