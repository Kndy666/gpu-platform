#!/usr/bin/env bash
set -euo pipefail
namespace="${1:-gpu-platform}"
if [[ ! "${namespace}" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then exit 1; fi
helm_path="$(command -v helm || true)"
if [[ -z "${helm_path}" ]]; then helm_path="$(dirname "$0")/../.tools/helm"; fi
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
systemctl disable --now gpu-platform-node.service 2>/dev/null || true
"${helm_path}" uninstall platform -n "${namespace}"
echo 'Application stopped. User workloads, permanent/work PVCs, registry data, database PVC, datasets and K3s are preserved.'
echo 'Use maintenance mode and stop active tasks before planned removal. No disks, namespaces or vendor components were deleted.'
