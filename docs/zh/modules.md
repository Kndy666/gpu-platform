# 模块指南

[返回首页](../../README.zh-CN.md) · [English](../en/modules.md)

在 `cluster.yaml` 中为每台主机选择插槽模块。十个扩展插槽覆盖从登录认证到容器执行的全部基础设施，内置模块与自定义第三方模块使用完全一致的契约。

## 内置模块列表

| 插槽 | 内置实现 | 职责 |
| --- | --- | --- |
| [认证](../../modules/auth/README.md) | `local` | 平台本地账号认证 |
| [通信](../../modules/transport/README.md) | `local`、`reverse-ssh` | 节点指令、环境间 SSH 中继与数据传输 |
| [登录入口](../../modules/entry/README.md) | `local`、`controller-forward` | 开发环境外部 SSH 连接入口 |
| [运行环境](../../modules/runtime/README.md) | `k3s` | 开发环境与批处理任务容器调度执行 |
| [存储](../../modules/storage/README.md) | `rawfile` | 带容量限制的持久化卷 |
| [镜像仓库](../../modules/registry/README.md) | `zot` | 共享镜像与私有镜像存储 |
| [镜像构建](../../modules/build/README.md) | `buildkit` | Rootless Dockerfile 构建 |
| [加速卡](../../modules/gpu/README.md) | `nvidia-mps`、`ascend` | NVIDIA 共享/整卡分配或昇腾 310P 独占分配 |
| [硬件监控](../../modules/metrics/README.md) | `host` | 带采样时间的 CPU、内存、GPU、磁盘与网络指标 |
| [通知](../../modules/notify/README.md) | `inapp`、`pushplus` | 控制台站内通知与微信 PushPlus 推送 |

纯 CPU 服务器和 ARM64 昇腾 310P 服务器配置示例：

```yaml
hosts:
  cpu-node:
    roles: [node]
    slots:
      gpu: null
  ascend-node:
    roles: [node]
    architecture: arm64
    slots:
      gpu:
        module: ascend
        options: {}
```

远程主机还需按[部署与运维](operations.md)配置通信与登录入口模块。昇腾镜像需要匹配宿主机的 CANN 版本；昇腾模块通过 `npu_count` 按整颗芯片分配（每卡算力配额 100% 允许 1 颗、200% 允许 2 颗，不足整颗向下取整）。

## NVIDIA GPU 分配模式

- **整卡算力（所申请每张卡均为 100%）**：容器直接使用预留设备上的原生 NVIDIA 驱动，保留单卡显存上限，并获得任务申请的全部 CPU 与系统内存。容器内使用 Rootless Docker 访问 GPU 需要整卡算力分配。
- **切分算力（任意一张卡低于 100%）**：整项任务通过独立 GPU worker 与专用 MPS 服务执行 SM 切分与显存限制，worker 占用任务 CPU 与内存额度的四分之一，容器内 CUDA 与 NCCL 调用经 Pod 回环转发。worker 异常会在自身额度内恢复；切分模式不支持 NVSHMEM 对称内存。

使用切分算力时需确保 K3s 可拉取 `ghcr.io/kndy666/gpu-platform-cuda-worker:0.4.0`，或通过 `slots.gpu.options.isolated_worker_image` 指定内部镜像地址。本地构建上下文位于 [modules/gpu/nvidia-mps/src/gpu_platform_gpu_nvidia_mps/images/Dockerfile](../../modules/gpu/nvidia-mps/src/gpu_platform_gpu_nvidia_mps/images/Dockerfile)。

## 开发自定义模块

1. 实现 [packages/contracts/src/gpu_platform_contracts/slots](../../packages/contracts/src/gpu_platform_contracts/slots) 中对应插槽的接口，可参考 [modules/auth/local/src/gpu_platform_auth_local/module.py](../../modules/auth/local/src/gpu_platform_auth_local/module.py)。
2. 声明 `ModuleDescriptor`（包含插槽名、版本、支持的交接物形式、资源定义、事件与基于 Pydantic `Options(Contract)` 的参数 schema）。
3. 通过 Python entry point 注册模块：

```toml
[project]
name = "gpu-platform-auth-directory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gpu-platform-contracts==0.4.0"]

[project.entry-points."gpu_platform.slots.auth"]
directory = "directory_auth.module:Module"
```

用户可见的资源名称和事件文案需通过 `LocalizedText.zh_hans` 提供简体中文翻译。将模块打包安装进派生控制/节点镜像和主机安装环境后，在 `cluster.yaml` 中更新镜像标签并指定模块名即可。

## 契约测试与主机所有权

- 使用 `ModuleContext` 获取主机标识、已校验选项和模块私有状态目录。
- 按稳定资源键与单位注册资源，使配额与申请表单自动支持；保证重试幂等。
- 使用 `HostOwnership` 记录模块新建的主机资源，确保卸载时只撤销模块自身变更并保留用户数据。
- 继承 `gpu_platform_contracts.testing` 中对应的可复用契约套件（定义见 [suites.py](../../packages/contracts/src/gpu_platform_contracts/testing/suites.py)），并使用 `uv run lint-imports` 检查依赖边界。
