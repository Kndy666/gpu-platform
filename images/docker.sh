#!/bin/bash
set -euo pipefail
umask 077
export XDG_RUNTIME_DIR=/run/user/1000
export DOCKER_HOST=unix:///run/user/1000/docker.sock
export DOCKER_CONFIG=/run/user/1000/docker-client
export XDG_CONFIG_HOME=/run/user/1000/docker-config
export DOCKERD_ROOTLESS_ROOTLESSKIT_FLAGS=--pidns
mkdir -p "$XDG_RUNTIME_DIR" "$DOCKER_CONFIG" /workspace/.docker /workspace/.platform
mkdir -p "$XDG_CONFIG_HOME/docker"
ln -s /etc/docker/certs.d "$XDG_CONFIG_HOME/docker/certs.d"
exec 9>/workspace/.docker/engine.lock
flock -n 9 || { echo '工作卷已有 Docker 引擎正在使用。' >&2; exit 73; }
if [[ -f /run/platform-registry/config.json ]]; then
    cp /run/platform-registry/config.json "$DOCKER_CONFIG/config.json"
fi
mkdir -p "$XDG_RUNTIME_DIR/cdi"
if compgen -G '/dev/nvidia[0-9]*' >/dev/null; then
    nvidia-ctk cdi generate --device-name-strategy=uuid --format=json \
        --output="$XDG_RUNTIME_DIR/cdi/nvidia.json"
    if [[ -S /run/platform-mps/control ]]; then
        # CDI applies these edits to every inner GPU container, including Compose.
        # The base image deliberately contains no Python runtime. jq performs
        # the small CDI edit and preserves the toolkit's driver mounts/hooks.
        jq --arg devices "$CUDA_VISIBLE_DEVICES" '.containerEdits.env += ["CUDA_MPS_PIPE_DIRECTORY=/run/platform-mps"] |
            .containerEdits.mounts += [{"hostPath":"/run/platform-mps","containerPath":"/run/platform-mps","options":["ro","bind","nosuid","nodev"]}] |
            (.devices[] | select(.name == "all") | .containerEdits.env) += ["CUDA_VISIBLE_DEVICES="+$devices]' \
            "$XDG_RUNTIME_DIR/cdi/nvidia.json" > "$XDG_RUNTIME_DIR/cdi/nvidia.json.tmp"
        mv "$XDG_RUNTIME_DIR/cdi/nvidia.json.tmp" "$XDG_RUNTIME_DIR/cdi/nvidia.json"
    fi
fi
cat > "$XDG_RUNTIME_DIR/daemon.json" <<'JSON'
{
  "data-root": "/workspace/.docker",
  "exec-root": "/run/user/1000/docker",
  "pidfile": "/run/user/1000/docker.pid",
  "hosts": ["unix:///run/user/1000/docker.sock"],
  "storage-driver": "overlay2",
  "features": {"containerd-snapshotter": false},
  "cdi-spec-dirs": ["/run/user/1000/cdi"],
  "log-driver": "local",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
JSON
exec dockerd-rootless.sh --config-file="$XDG_RUNTIME_DIR/daemon.json"
