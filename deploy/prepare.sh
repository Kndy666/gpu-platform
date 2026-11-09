#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
case "$(uname -m)" in aarch64) target_arch=arm64;; x86_64) target_arch=amd64;; *) exit 1;; esac
helm_version=v3.19.0
mkdir -p "${project_root}/.tools"
download_dir="$(mktemp -d)"
trap 'rm -rf "${download_dir}"' EXIT
if [[ "${1:-}" != --nerdctl-only ]]; then
asset="helm-${helm_version}-linux-${target_arch}.tar.gz"
curl -fsSL --connect-timeout 10 --max-time 120 "https://mirrors.huaweicloud.com/helm/${helm_version}/${asset}" -o "${download_dir}/${asset}" || \
  curl -fsSL --connect-timeout 10 --max-time 120 "https://get.helm.sh/${asset}" -o "${download_dir}/${asset}"
curl -fsSL --connect-timeout 10 --max-time 60 "https://get.helm.sh/${asset}.sha256sum" -o "${download_dir}/checksum"
(cd "${download_dir}" && sha256sum -c checksum)
tar xzf "${download_dir}/${asset}" -C "${download_dir}" "linux-${target_arch}/helm"
install -m 0755 "${download_dir}/linux-${target_arch}/helm" "${project_root}/.tools/helm"
"${project_root}/.tools/helm" version --short
fi
nerdctl_version="$(awk -F= '$1 == "nerdctl" {print $2}' "${project_root}/deploy/vendor-versions.lock")"
if [[ ! -x "${project_root}/.tools/nerdctl" ]]; then
  asset="nerdctl-${nerdctl_version}-linux-${target_arch}.tar.gz"
  release="https://github.com/containerd/nerdctl/releases/download/v${nerdctl_version}"
  curl -fsSL --connect-timeout 10 --max-time 120 "${release}/SHA256SUMS" -o "${download_dir}/checksums"
  curl -fsSL --connect-timeout 10 --max-time 180 "https://ghfast.top/${release}/${asset}" -o "${download_dir}/${asset}" || \
    curl -fsSL --connect-timeout 10 --max-time 180 "${release}/${asset}" -o "${download_dir}/${asset}"
  expected="$(awk -v name="${asset}" '$2 == name {print $1}' "${download_dir}/checksums")"
  [[ "${expected}" =~ ^[a-f0-9]{64}$ ]]
  printf '%s  %s\n' "${expected}" "${asset}" > "${download_dir}/checksum"
  (cd "${download_dir}" && sha256sum -c checksum)
  tar xzf "${download_dir}/${asset}" -C "${download_dir}" nerdctl
  install -m 0755 "${download_dir}/nerdctl" "${project_root}/.tools/nerdctl"
fi
