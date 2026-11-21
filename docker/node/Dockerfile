ARG PYTHON_IMAGE=python:3.12.12-slim-bookworm
ARG DOCKER_CLI_IMAGE=docker:29.1.3-cli
FROM ${DOCKER_CLI_IMAGE} AS docker_cli
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLATFORM_DATA=/var/lib/gpu-platform \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH
WORKDIR /opt/gpu-platform
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client ca-certificates curl iproute2 iptables util-linux e2fsprogs fuse3 procps \
    && rm -rf /var/lib/apt/lists/*
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY packages/ packages/
COPY modules/ modules/
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.13.8 \
    && uv sync --locked --no-dev --no-editable \
      --package gpu-platform-node --package gpu-platform-transport-local \
      --package gpu-platform-transport-reverse-ssh --package gpu-platform-entry-local \
      --package gpu-platform-entry-controller-forward --package gpu-platform-runtime-k3s \
      --package gpu-platform-storage-rawfile --package gpu-platform-registry-zot \
      --package gpu-platform-build-buildkit --package gpu-platform-gpu-nvidia-mps --package gpu-platform-gpu-ascend \
      --package gpu-platform-metrics-host \
    && rm -rf /root/.cache/uv
CMD ["python", "-m", "gpu_platform_node.daemon", "serve", "--config", "/etc/gpu-platform/platform.yaml"]
