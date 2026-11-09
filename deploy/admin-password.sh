#!/usr/bin/env bash
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
k3s kubectl -n "${1:-gpu-platform}" get secret platform-secrets -o 'jsonpath={.data.initial-admin-password}' | base64 -d
printf '\n'
