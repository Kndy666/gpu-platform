# 参与开发

[首页](../../README.zh-CN.md) · [English](../en/development.md)

工作区使用 uv。控制中心和节点容器运行 Python 3.12，公共定义包与主机安装工具同时支持 Python 3.10。

```bash
uv sync --locked --all-packages
uv run pytest
uv run ruff check packages modules tests
uv run mypy packages/*/src modules/*/*/src
uv run lint-imports
```

## 本地启动面板

使用独立的开发数据库和状态目录：

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

打开 `http://127.0.0.1:8000`。执行任务还需要配置好的节点，准备方法见[部署指南](operations.md)。

## 修改项目

通过[架构指南](architecture.md)找到功能所属的包；扩展基础设施时参考[模块指南](modules.md)。面板通过控制中心公开服务调用业务功能，模块实现通过 entry points 加载。

验证可观察行为、资源记账、授权、状态转换和失败处理。测试使用隔离 fixture 和可注入依赖。修复问题后使用已有功能测试和直接功能检查，断言行为与结构化数据；文案、翻译和页面快照不作为测试目标。

面向用户的源码文字使用英文，并提供简体中文翻译。Agent skill、安装输出、日志和配置注释使用英文。新数据库直接创建当前结构。

需要 Docker 或加速卡的检查各有独立说明：

- [镜像构建与仓库](../../modules/build/buildkit/tests/live/README.md)
- [开发环境](../../modules/runtime/k3s/tests/live/README.md)
- [CUDA 与 NCCL](../../modules/gpu/nvidia-mps/tests/live/README.md)
