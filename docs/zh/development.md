# 开发指南

[返回首页](../../README.zh-CN.md) · [English](../en/development.md)

Python 工作区使用 `uv` 管理（控制与节点容器使用 Python 3.12，`contracts` 与主机安装器兼容 Python 3.10+）。位于 `packages/panel` 的独立前端面板使用 Node 22+、Vue 3、TypeScript、Vite、Tailwind CSS v4 和 Reka UI。

## 常用检查命令

Python 后端与模块检查：

```bash
uv sync --locked --all-packages
uv run pytest
uv run ruff check packages modules tests
uv run mypy packages/contracts/src packages/control/src packages/node/src packages/installer/src modules/*/*/src
uv run lint-imports
DJANGO_SETTINGS_MODULE=gpu_platform_control.settings.test uv run python -m gpu_platform_control.manage check
DJANGO_SETTINGS_MODULE=gpu_platform_control.settings.test uv run python -m gpu_platform_control.manage check_translations --compile
```

前端面板检查（`packages/panel`）：

```bash
cd packages/panel
npm ci
npm run check:i18n
npm run typecheck
npm run test
npm run build
```

## 本地启动调试

先在独立开发状态目录下启动控制服务 API：

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

另开一个终端启动 Vue 开发服务器（自动将 `/api/` 与 `/healthz` 代理至 `http://127.0.0.1:8000`）：

```bash
cd packages/panel
npm run dev
```

打开 `http://127.0.0.1:5173`。如需实际启动容器任务，请参考[部署与运维](operations.md)准备执行节点。

## 重新生成 OpenAPI TypeScript 类型

修改 `packages/control` 的接口或 schema 后，导出最新 OpenAPI 文档并更新前端类型定义：

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

## 开发约定

- 具体基础设施实现保留在 `modules/<slot>/<implementation>`，通过 entry points 加载。
- `packages/panel` 完全通过 `/api/v1` 与后端解耦。
- `packages/panel` 面向最新版 Chromium、Firefox 与 Safari，使用原生 Web 特性，不引入旧浏览器兼容垫片。
- 测试重点覆盖可观察行为、接口契约、资源记账、权限、状态流转与错误处理，不以文案、翻译或页面文本快照作为断言目标。
- 需要真实 Docker 或加速卡硬件的集成测试说明：
  - [镜像构建与仓库](../../modules/build/buildkit/tests/live/README.md)
  - [开发环境运行](../../modules/runtime/k3s/tests/live/README.md)
  - [CUDA 与 NCCL](../../modules/gpu/nvidia-mps/tests/live/README.md)
