#!/usr/bin/env bash
set -euo pipefail
[[ "${EUID}" == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
toolkit_version=1.20.1-1
installed_version="$(dpkg-query -W -f='${Version}' nvidia-container-toolkit 2>/dev/null || true)"
if command -v nvidia-container-runtime >/dev/null && dpkg --compare-versions "${installed_version:-0}" ge "$toolkit_version"; then
  nvidia-container-runtime --version; exit 0
fi
mkdir -p /usr/share/keyrings
if [[ ! -e /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg ]]; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
fi
if [[ ! -e /etc/apt/sources.list.d/nvidia-container-toolkit.list ]]; then
  printf '%s\n' 'deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/$(ARCH) /' > /etc/apt/sources.list.d/nvidia-container-toolkit.list
fi
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  "nvidia-container-toolkit=${toolkit_version}" "nvidia-container-toolkit-base=${toolkit_version}" \
  "libnvidia-container-tools=${toolkit_version}" "libnvidia-container1=${toolkit_version}"
echo 'Toolkit installed. Existing NVIDIA driver and Docker configuration were not changed.'
