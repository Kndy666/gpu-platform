#!/bin/bash
set -euo pipefail
umask 077
source "${1:-/run/platform/task.env}"
[[ "${TASK_USER:?}" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,31}$ ]] || exit 64
[[ "${TASK_HOME:?}" == "/home/$TASK_USER" ]] || exit 64
sudo -n /bin/bash -s -- "$TASK_USER" "$TASK_HOME" <<'IDENTITY'
set -euo pipefail
name=$1
home_dir=$2
old=$(getent passwd 1000 | cut -d: -f1)
[[ -n "$old" && $(awk -F: '$3==1000 {n++} END {print n+0}' /etc/passwd) == 1 ]] || exit 65
existing=$(getent passwd "$name" || true)
[[ -z "$existing" || $(cut -d: -f3 <<<"$existing") == 1000 ]] || exit 65
existing_group=$(getent group "$name" || true)
[[ -z "$existing_group" || $(cut -d: -f3 <<<"$existing_group") == 1000 ]] || exit 65
rewrite() {
    local file=$1 expression=$2 temporary
    temporary=$(mktemp "$file.platform.XXXXXX")
    awk -F: -v OFS=: -v name="$name" -v old="$old" -v home_dir="$home_dir" "$expression" "$file" > "$temporary"
    chown --reference="$file" "$temporary"
    chmod --reference="$file" "$temporary"
    mv "$temporary" "$file"
}
rewrite /etc/passwd '$3==1000 {$1=name; $6=home_dir} {print}'
rewrite /etc/group '$3==1000 {$1=name} {print}'
rewrite /etc/shadow '$1==old {$1=name} {print}'
if [[ -f /etc/gshadow ]]; then rewrite /etc/gshadow '$1==old {$1=name} {print}'; fi
for file in /etc/subuid /etc/subgid; do
    [[ ! -f "$file" ]] || rewrite "$file" '$1==old {$1=name} {print}'
done
printf '#1000 ALL=(ALL:ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/platform-user
chmod 0440 /etc/sudoers.d/platform-user
visudo -cf /etc/sudoers.d/platform-user >/dev/null
rm -f /etc/sudoers.d/user
IDENTITY
export HOME="$TASK_HOME" USER="$TASK_USER" LOGNAME="$TASK_USER"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-${TASK_CPU:?}}" OMP_THREAD_LIMIT="${OMP_THREAD_LIMIT:-$TASK_CPU}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-$TASK_CPU}" MKL_NUM_THREADS="${MKL_NUM_THREADS:-$TASK_CPU}"
mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"
for file in .bashrc .profile .bash_logout; do
    [[ -e "$HOME/$file" || ! -f "/etc/skel/$file" ]] || cp -n "/etc/skel/$file" "$HOME/$file"
done
mkdir -p /run/user
docker_pid=''
if [[ -x /opt/platform/docker.sh ]]; then
    export XDG_RUNTIME_DIR=/run/user/1000 DOCKER_HOST=unix:///run/user/1000/docker.sock
    export DOCKER_CONFIG=/run/user/1000/docker-client
    mkdir -p /workspace/.platform
    setsid /opt/platform/docker.sh >> /workspace/.platform/docker.log 2>&1 &
    docker_pid=$!
    for attempt in $(seq 1 60); do
        if docker info >/dev/null 2>&1; then break; fi
        if ! kill -0 "$docker_pid" 2>/dev/null || [[ "$attempt" == 60 ]]; then
            tail -n 40 /workspace/.platform/docker.log >&2
            kill -TERM -- "-$docker_pid" 2>/dev/null || true
            exit 71
        fi
        sleep 1
    done
fi
shutdown_docker() {
    [[ -n "$docker_pid" ]] || return 0
    local containers
    containers=$(docker ps -q 2>/dev/null) || containers=''
    if [[ -n "$containers" ]]; then
        timeout 15 xargs -r docker stop --timeout 10 <<< "$containers" >/dev/null 2>&1 || true
    fi
    kill -TERM -- "-$docker_pid" 2>/dev/null || true
    for _ in $(seq 1 15); do kill -0 "$docker_pid" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$docker_pid" 2>/dev/null || true
    wait "$docker_pid" 2>/dev/null || true
    docker_pid=''
}
trap shutdown_docker EXIT
[[ -f /run/user/ssh_host_ed25519_key ]] || ssh-keygen -q -t ed25519 -N '' -f /run/user/ssh_host_ed25519_key
cat > /run/user/sshd_config <<'CONF'
Port 2222
ListenAddress 0.0.0.0
HostKey /run/user/ssh_host_ed25519_key
PidFile /run/user/sshd.pid
AuthorizedKeysFile none
AuthorizedKeysCommand /usr/bin/cat /run/platform/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
UsePAM no
AllowTcpForwarding yes
GatewayPorts no
X11Forwarding no
PermitTunnel no
PrintMotd no
StrictModes yes
Subsystem sftp internal-sftp
CONF
printf 'AuthorizedKeysCommandUser %s\nAllowUsers %s\n' "$TASK_USER" "$TASK_USER" >> /run/user/sshd_config
printf 'SetEnv' >> /run/user/sshd_config
for name in PATH LD_LIBRARY_PATH PYTHONPATH VIRTUAL_ENV CONDA_PREFIX CUDA_HOME CUDA_PATH CUDA_VISIBLE_DEVICES CUDA_MPS_PIPE_DIRECTORY LANG LC_ALL OMP_NUM_THREADS OMP_THREAD_LIMIT OPENBLAS_NUM_THREADS MKL_NUM_THREADS DOCKER_HOST DOCKER_CONFIG XDG_RUNTIME_DIR ${TASK_ENV_NAMES:-}; do
    if [[ -v "$name" ]]; then
        value="${!name}"; value="${value//\\/\\\\}"; value="${value//\"/\\\"}"
        if [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]]; then
            printf ' "%s=%s"' "$name" "$value" >> /run/user/sshd_config
        fi
    fi
done
printf '\n' >> /run/user/sshd_config
setsid /usr/sbin/sshd -D -e -f /run/user/sshd_config &
ssh_pid=$!
command_pid=''
stopped=0
stop() {
    stopped=1
    [[ -z "$command_pid" ]] || kill -TERM -- "-$command_pid" 2>/dev/null || true
    kill -TERM -- "-$ssh_pid" 2>/dev/null || true
}
trap stop TERM INT
sleep .2
kill -0 "$ssh_pid" 2>/dev/null || exit 70
if [[ "$TASK_MODE" == interactive ]]; then
    set +e
    wait "$ssh_pid"
    code=$?
    (( stopped == 0 )) || exit 143
    exit "$code"
fi
mkdir -p /workspace/.platform
cd "$TASK_DIRECTORY"
setsid /bin/bash /run/platform/command.sh \
    > >(tee -a /workspace/.platform/stdout.log) \
    2> >(tee -a /workspace/.platform/stderr.log >&2) &
command_pid=$!
set +e
wait "$command_pid"
code=$?
if (( stopped )); then
    for _ in $(seq 1 25); do kill -0 "$command_pid" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$command_pid" 2>/dev/null || true
    wait "$command_pid" 2>/dev/null
    code=143
fi
kill -TERM -- "-$command_pid" 2>/dev/null || true
printf '{"exit_code":%d,"ended_at_unix":%s}\n' "$code" "$(date +%s)" > /workspace/.platform/result.json.tmp
sync -f /workspace/.platform/result.json.tmp
mv /workspace/.platform/result.json.tmp /workspace/.platform/result.json
kill -TERM -- "-$ssh_pid" 2>/dev/null || true
wait "$ssh_pid" 2>/dev/null
shutdown_docker
wait
exit "$code"
