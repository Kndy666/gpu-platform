#!/usr/bin/env bash
set -euo pipefail
[[ "${EUID}" == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config_path="${1:-${project_root}/config.yaml}"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
python3 "${project_root}/deploy/install.py" --config "${config_path}" --render-only
resolved_config="${project_root}/runtime/deploy/resolved-config.yaml"
python3 "${project_root}/deploy/workspace-runtime.py" --config "${resolved_config}"
python3 "${project_root}/deploy/resource-view.py" --config "${resolved_config}"
python3 "${project_root}/deploy/cluster_resources.py" --config "${resolved_config}"
image="$(python3 - "${config_path}" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['platform']['app_image'])
PY
)"
namespace="$(python3 - "${config_path}" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['platform']['namespace'])
PY
)"
role="$(python3 - "${resolved_config}" <<'PY'
import sys,yaml
print(yaml.safe_load(open(sys.argv[1]))['platform'].get('role', 'controller'))
PY
)"
if [[ "${role}" == controller ]]; then
  docker build --network=host \
    --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12.12-slim-bookworm}" \
    -t "${image}" "${project_root}"
  python3 "${project_root}/deploy/import-local-image.py" "${image}"
  revision="$(docker image inspect "${image}" --format '{{.Id}}')"
else
  revision="${image}"
fi
"${project_root}/.tools/helm" upgrade --install platform "${project_root}/deploy/helm" -n "${namespace}" \
  -f "${project_root}/runtime/deploy/app-values.yaml" --set-string "imageRevision=${revision}" --wait --timeout 3m
python3 - "${config_path}" "${project_root}" <<'PY'
import importlib.util,sys,yaml
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[2])/'deploy'))
spec=importlib.util.spec_from_file_location('platform_installer',Path(sys.argv[2])/'deploy/install.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.install_workspace_security()
cfg=yaml.safe_load((Path(sys.argv[2])/'runtime/deploy/resolved-config.yaml').read_text())
module.run(['k3s','kubectl','label','namespace',cfg['platform']['workload_namespace'],'pod-security.kubernetes.io/enforce=baseline','--overwrite'])
module.install_node_agent(cfg)
from node_services import install_node_services
install_node_services(cfg, Path(sys.argv[2]))
PY
