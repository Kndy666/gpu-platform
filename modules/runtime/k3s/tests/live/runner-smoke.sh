#!/usr/bin/env bash
set -euo pipefail
module_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
case ",${TEST_FEATURES:-}," in
  *,docker,*) ;;
  *) echo 'SKIP Docker smoke test (enable TEST_FEATURES=docker)'; exit 0 ;;
esac
for tool in docker python3 ssh ssh-keygen scp; do
  if ! command -v "${tool}" >/dev/null; then echo "SKIP Required command unavailable: ${tool}"; exit 0; fi
done
image="${TEST_ENVIRONMENT_IMAGE:?Set TEST_ENVIRONMENT_IMAGE to the image under test}"
apparmor_profile="${TEST_APPARMOR_PROFILE:?Set TEST_APPARMOR_PROFILE to the installed profile under test}"
docker info >/dev/null
if ! docker image inspect "${image}" >/dev/null 2>&1; then
  echo "SKIP Local test image unavailable: ${image}"
  exit 0
fi
name="gpu-runner-smoke-$$"
if [[ ! -c /dev/net/tun ]] || ! test -r /sys/kernel/security/apparmor/profiles || ! grep -Fq "${apparmor_profile} " /sys/kernel/security/apparmor/profiles; then
  echo "SKIP Workspace AppArmor profile and /dev/net/tun are required"
  exit 0
fi
test_dir="$(mktemp -d -t gpu-runner-smoke.XXXXXX)"
cleanup() { docker rm -f "${name}" >/dev/null 2>&1 || true; rm -rf "${test_dir}"; }
trap cleanup EXIT
mkdir -p "${test_dir}"/{config,work,home}
ssh-keygen -q -t ed25519 -N '' -f "${test_dir}/identity"
cp "${test_dir}/identity.pub" "${test_dir}/config/authorized_keys"
chmod 755 "${test_dir}/config"
chmod 644 "${test_dir}/config/authorized_keys"
printf 'TASK_MODE=batch\nTASK_DIRECTORY=/workspace\nTASK_USER=researcher\nTASK_HOME=/home/researcher\nTASK_CPU=2\nTASK_ENV_NAMES=\n' > "${test_dir}/config/task.env"
printf '#!/bin/bash\necho training\nsleep 30\necho finished\nexit 42\n' > "${test_dir}/config/command.sh"
cp "${module_root}/src/gpu_platform_runtime_k3s/images/runner.sh" "${test_dir}/config/runner.sh"
chmod 644 "${test_dir}/config/"*
python3 - "${module_root}/src" "${test_dir}/seccomp.json" "$(docker image inspect --format '{{.Architecture}}' "${image}")" <<'PYCODE'
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from gpu_platform_runtime_k3s.host.workspace_security import seccomp_profile
Path(sys.argv[2]).write_text(json.dumps(seccomp_profile(sys.argv[3])))
PYCODE
docker run --rm --user 0 -v "${test_dir}/work:/workspace" -v "${test_dir}/home:/home/researcher" --entrypoint chown "${image}" 1000:1000 /workspace /home/researcher
docker run -d --name "${name}" --entrypoint /bin/bash --user 1000:1000 --security-opt seccomp="${test_dir}/seccomp.json" --security-opt apparmor="${apparmor_profile}" \
  --security-opt systempaths=unconfined --device /dev/net/tun \
  -p 127.0.0.1::2222 --tmpfs /run/user:uid=1000,gid=1000,mode=0700 \
  -v "${test_dir}/config:/run/platform:ro" -v "${test_dir}/work:/workspace" -v "${test_dir}/home:/home/researcher" "${image}" /run/platform/runner.sh /run/platform/task.env >/dev/null
address="$(docker port "${name}" 2222/tcp)"
port="${address##*:}"
ssh_opts=(-i "${test_dir}/identity" -p "${port}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="${test_dir}/known_hosts" -o ConnectTimeout=2)
for attempt in $(seq 1 90); do
  if ssh "${ssh_opts[@]}" researcher@127.0.0.1 'echo connected' > "${test_dir}/ssh.log" 2>/dev/null; then break; fi
  if [[ "${attempt}" == 90 ]]; then docker logs "${name}"; exit 1; fi
  sleep 1
done
ssh "${ssh_opts[@]}" researcher@127.0.0.1 'test "$(sudo -n id -u)" = 0; bwrap --unshare-all --ro-bind / / true; test -s ~/.bashrc'
head -c 1024 /dev/urandom > "${test_dir}/marker"
scp -q -i "${test_dir}/identity" -P "${port}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile="${test_dir}/known_hosts" "${test_dir}/marker" researcher@127.0.0.1:/workspace/marker
ssh "${ssh_opts[@]}" researcher@127.0.0.1 'sha256sum /workspace/marker' | cut -d' ' -f1 > "${test_dir}/remote.sha256"
sha256sum "${test_dir}/marker" | cut -d' ' -f1 > "${test_dir}/local.sha256"
cmp "${test_dir}/local.sha256" "${test_dir}/remote.sha256"
ssh "${ssh_opts[@]}" researcher@127.0.0.1 'sleep 60' >/dev/null 2>&1 &
ssh_client=$!
sleep 1
kill "${ssh_client}"; wait "${ssh_client}" || true
[[ "$(docker inspect -f '{{.State.Running}}' "${name}")" == true ]]
exit_code="$(docker wait "${name}")"
[[ "${exit_code}" == 42 ]]
python3 - "${test_dir}/work/.platform/result.json" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))['exit_code']==42
PY
test -s "${test_dir}/work/.platform/stdout.log"
echo 'SSH, file transfer, disconnected task execution, logs and exit status: PASS'
