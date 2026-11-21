# Compute resource platform

A modular platform for development environments, batch jobs, image builds, and shared compute resources. The Django panel and `/api/v1` use one control service for accounts, approvals, quotas, scheduling, and audit records. Each node assembles its execution environment through configured modules.

The workspace includes ten replaceable slots: authentication, node transport, login entry, runtime, storage, registry, builds, GPU allocation, metrics, and notifications. The default execution stack uses K3s, Kueue, Rawfile, Zot, rootless BuildKit, and NVIDIA MPS. Platform services run in Docker Compose.

[中文说明](docs/zh/operations.md) · [Architecture](docs/en/architecture.md) · [Operations](docs/en/operations.md) · [API](docs/en/api.md) · [Security](docs/en/security.md) · [Module development](docs/en/modules.md)

## Development

Python 3.12 runs the control service and node containers. The contracts and installer support Python 3.10.

```bash
uv sync --locked --all-packages
uv run pytest
uv run ruff check packages modules tests
uv run mypy packages/*/src modules/*/*/src
uv run lint-imports
```

Start the panel with an isolated development database:

```bash
export DJANGO_DEBUG=1 PLATFORM_HTTPS=0
export PLATFORM_DATA="$PWD/runtime/development"
export PLATFORM_CONFIG="$PWD/config/platform.default.yaml"
export PLATFORM_HOST=main
export PLATFORM_INITIAL_ADMIN_PASSWORD=change-this-development-password
uv run python -m gpu_platform_control.manage initdb
uv run python -m gpu_platform_control.manage bootstrap
uv run python -m gpu_platform_control.manage runserver 127.0.0.1:8000
```

Task execution requires a configured node and its selected infrastructure. [Operations](docs/en/operations.md) covers host installation and image preparation.

## Deployment

Copy the default configuration and set the public address, host architecture, data paths, and enabled modules:

```bash
cp config/platform.default.yaml cluster.yaml
uv run platformctl validate --config cluster.yaml --host main
uv run platformctl plan --config cluster.yaml --host main
```

Use the same cluster file on every machine. `platformctl install --config cluster.yaml --host NAME` checks that machine, installs module prerequisites, generates its Compose configuration, and starts its services. Ubuntu 22.04 and 24.04 on AMD64 and ARM64 are supported. Mount data disks before installation; the NVIDIA module also requires a suitable driver.

The panel follows browser language settings until a user selects English or Simplified Chinese. The selected language is saved to the account. Agent instructions and installer output use English.
