#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
image="${WORKSPACE_TEST_IMAGE:-gpu-workspace:0.2.0}"
name="gpu-runner-smoke-$$"
mkdir -p "${project_root}/runtime"
test_dir="$(mktemp -d "${project_root}/runtime/runner.XXXXXX")"
cleanup() { docker rm -f "${name}" >/dev/null 2>&1 || true; rm -rf "${test_dir}"; }
trap cleanup EXIT
mkdir -p "${test_dir}"/{config,work,home}
ssh-keygen -q -t ed25519 -N '' -f "${test_dir}/identity"
cp "${test_dir}/identity.pub" "${test_dir}/config/authorized_keys"
chmod 755 "${test_dir}/config"
chmod 644 "${test_dir}/config/authorized_keys"
printf 'TASK_MODE=batch\nTASK_DIRECTORY=/workspace\nTASK_USER=researcher\nTASK_HOME=/home/researcher\nTASK_CPU=2\nTASK_ENV_NAMES=\n' > "${test_dir}/config/task.env"
printf '#!/bin/bash\necho training\nsleep 30\necho finished\nexit 42\n' > "${test_dir}/config/command.sh"
cp "${project_root}/images/runner.sh" "${test_dir}/config/runner.sh"
chmod 644 "${test_dir}/config/"*
docker run -d --name "${name}" --entrypoint /bin/bash --user 1000:1000 --security-opt seccomp="${project_root}/deploy/profiles/workspace-seccomp.json" --security-opt apparmor=gpu-platform-workspace \
  --security-opt systempaths=unconfined --device /dev/net/tun \
  -p 127.0.0.1:18022:2222 --tmpfs /run/user:uid=1000,gid=1000,mode=0700 \
  -v "${test_dir}/config:/run/platform:ro" -v "${test_dir}/work:/workspace" -v "${test_dir}/home:/home/researcher" "${image}" /run/platform/runner.sh /run/platform/task.env >/dev/null
ssh_opts=(-i "${test_dir}/identity" -p 18022 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="${test_dir}/known_hosts" -o ConnectTimeout=2)
for attempt in $(seq 1 12); do
  if ssh "${ssh_opts[@]}" researcher@127.0.0.1 'echo connected' > "${test_dir}/ssh.log" 2>/dev/null; then break; fi
  if [[ "${attempt}" == 12 ]]; then docker logs "${name}"; exit 1; fi
  sleep 1
done
ssh "${ssh_opts[@]}" researcher@127.0.0.1 'test "$(sudo -n id -u)" = 0; bwrap --unshare-all --ro-bind / / true; test -s ~/.bashrc; grep -q force_color_prompt=yes ~/.bashrc'
printf 'sftp-marker\n' > "${test_dir}/marker"
scp -q -i "${test_dir}/identity" -P 18022 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile="${test_dir}/known_hosts" "${test_dir}/marker" researcher@127.0.0.1:/workspace/marker
ssh "${ssh_opts[@]}" researcher@127.0.0.1 'test "$(cat /workspace/marker)" = sftp-marker'
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
rg -q 'finished' "${test_dir}/work/.platform/stdout.log"
echo 'Non-root SSH, scp/sftp, command survival after SSH disconnection, durable logs and exit 42: PASS'
