# Development

[Home](../../README.md) · [简体中文](../zh/development.md)

The Python workspace uses `uv` (Python 3.12 for control and node containers; Python 3.10+ for contracts and installer). The standalone web panel in `packages/panel` uses Node 22+, Vue 3, TypeScript, Vite, Tailwind CSS v4, and Reka UI.

## Verification commands

Backend and Python packages:

```bash
uv sync --locked --all-packages
uv run pytest
uv run ruff check packages modules tests
uv run mypy packages/contracts/src packages/control/src packages/node/src packages/installer/src modules/*/*/src
uv run lint-imports
DJANGO_SETTINGS_MODULE=gpu_platform_control.settings.test uv run python -m gpu_platform_control.manage check
DJANGO_SETTINGS_MODULE=gpu_platform_control.settings.test uv run python -m gpu_platform_control.manage check_translations --compile
```

Frontend panel (`packages/panel`):

```bash
cd packages/panel
npm ci
npm run check:i18n
npm run typecheck
npm run test
npm run build
```

## Run locally

Start the control API against an isolated development state directory:

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

In a second terminal, start the Vue panel dev server (proxies `/api/` and `/healthz` to `http://127.0.0.1:8000`):

```bash
cd packages/panel
npm run dev
```

Open `http://127.0.0.1:5173`. Executing real tasks also requires a configured execution node; see [deployment and operations](operations.md).

## Regenerate OpenAPI types

When changing endpoints or schemas in `packages/control`, export the updated OpenAPI schema and regenerate TypeScript definitions:

```bash
DJANGO_SETTINGS_MODULE=gpu_platform_control.settings.test uv run python -c "
import django, json
django.setup()
from gpu_platform_control.api import api
print(json.dumps(api.get_openapi_schema(), ensure_ascii=False))
" > /tmp/gpu-platform-openapi.json
cd packages/panel && npx openapi-typescript /tmp/gpu-platform-openapi.json -o src/api/openapi.d.ts
rm /tmp/gpu-platform-openapi.json
```

## Conventions

- Keep concrete infrastructure inside `modules/<slot>/<implementation>` and load implementations via entry points.
- Keep `packages/panel` decoupled from Django internals; communicate solely through `/api/v1`.
- Target evergreen modern browsers (latest Chromium, Firefox, Safari) in `packages/panel` without legacy polyfills.
- Test observable behavior, contracts, resource accounting, authorization, state transitions, and failure handling using isolated fixtures. Keep prose, translations, and text snapshots outside test assertions.
- Author user-facing text in English with Simplified Chinese translations; keep agent skills, installer output, logs, and configuration comments in English.
- Live hardware integration tests have local instructions:
  - [Image builds and registry](../../modules/build/buildkit/tests/live/README.md)
  - [Development environment](../../modules/runtime/k3s/tests/live/README.md)
  - [CUDA and NCCL](../../modules/gpu/nvidia-mps/tests/live/README.md)
