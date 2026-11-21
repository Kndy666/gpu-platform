# Development

[Home](../../README.md) · [简体中文](../zh/development.md)

The workspace uses uv. Control and node containers run Python 3.12; contracts and the host installer also support Python 3.10.

```bash
uv sync --locked --all-packages
uv run pytest
uv run ruff check packages modules tests
uv run mypy packages/*/src modules/*/*/src
uv run lint-imports
```

## Run the panel locally

Use a separate development database and state directory:

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

Open `http://127.0.0.1:8000`. Running tasks also requires a configured execution node; follow [deployment](operations.md) to prepare it.

## Make a change

Use the [architecture guide](architecture.md) to locate the responsible package and the [module guide](modules.md) for infrastructure extensions. Keep panel calls inside the public control service boundary. Load module implementations through entry points.

Verify observable behavior, resource accounting, authorization, state transitions, and failure handling. Use isolated fixtures and injected dependencies. After a fix, use existing functional tests and direct checks. Keep assertions about behavior and structured data; prose, translations, and page snapshots are outside test objectives.

User-facing source text is English with Simplified Chinese translations. Agent skills, installer output, logs, and configuration comments use English. New databases create the current schema directly.

Tests that need Docker or accelerator hardware have local instructions:

- [Image builds and registry](../../modules/build/buildkit/tests/live/README.md)
- [Development environment](../../modules/runtime/k3s/tests/live/README.md)
- [CUDA and NCCL](../../modules/gpu/nvidia-mps/tests/live/README.md)
