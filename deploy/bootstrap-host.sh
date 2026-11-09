#!/usr/bin/env bash
# Fresh Ubuntu 22.04 ARM64 host. Existing data filesystems are never formatted.
set -euo pipefail
[[ "${EUID}" == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config_path="${1:-${project_root}/config.yaml}"
[[ -f "${config_path}" ]] || { echo 'Copy config.example.yaml to config.yaml and set the server address and mounted data disks.' >&2; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 22.04 && "$(uname -m)" == aarch64 ]] || { echo 'This prepared deployment targets Ubuntu 22.04 ARM64.' >&2; exit 1; }
command -v nvidia-smi >/dev/null || { echo 'Install NVIDIA R620 (620.70.06 verified) and reboot before deployment.' >&2; exit 1; }
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-yaml python3-venv ca-certificates curl git \
  docker.io iproute2 iptables util-linux openssl gnupg apparmor apparmor-utils
systemctl enable --now docker
"${project_root}/deploy/install.sh" --config "${config_path}" --preflight
image="$(python3 - "${config_path}" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['platform']['app_image'])
PY
)"
docker build --network=host \
  --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12.12-slim-bookworm}" \
  -t "${image}" "${project_root}"
"${project_root}/deploy/prepare.sh"
"${project_root}/deploy/install.sh" --config "${config_path}"
