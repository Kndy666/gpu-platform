# 用模块适配自己的场景

[首页](../../README.zh-CN.md) · [English](../en/modules.md)

在 `cluster.yaml` 中为每台主机选择模块。十个插槽覆盖认证到任务执行的基础设施选择，可以使用内置实现，也可以编写自己的扩展。

## 选择起点

| 插槽 | 内置实现 | 用途 |
| --- | --- | --- |
| [账号认证](../../modules/auth/README.md) | `local` | 平台账号登录 |
| [节点通信](../../modules/transport/README.md) | `local`、`reverse-ssh` | 指令、连接与数据传输 |
| [登录入口](../../modules/entry/README.md) | `local`、`controller-forward` | 连接开发环境的 SSH 入口 |
| [任务运行](../../modules/runtime/README.md) | `k3s` | 开发环境与批处理程序 |
| [存储](../../modules/storage/README.md) | `rawfile` | 有容量限制的持久卷 |
| [镜像仓库](../../modules/registry/README.md) | `zot` | 私有和共享镜像 |
| [镜像构建](../../modules/build/README.md) | `buildkit` | Dockerfile 构建 |
| [加速卡](../../modules/gpu/README.md) | `nvidia-mps`、`ascend` | NVIDIA GPU 共享或昇腾 310P 独占分配 |
| [性能监控](../../modules/metrics/README.md) | `host` | 带采样时间的硬件读数 |
| [消息通知](../../modules/notify/README.md) | `inapp`、`pushplus` | 面板站内通知和管理员微信提醒 |

纯 CPU 服务器可以关闭加速卡插槽。已安装驱动的 ARM64 昇腾 310P 服务器可以选择 Ascend：

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

将这些选择合并到主机配置，远程节点还需按[部署指南](operations.md)配置通信与登录入口。Ascend 镜像需包含匹配的 CANN 环境。模块通过 `npu_count` 分配整颗芯片；单卡算力额度 100% 对应一颗、200% 对应两颗，不足一颗的部分向下取整。

## NVIDIA GPU 分配

当申请的每张卡都是 100% 算力时，工作区直接使用原生 NVIDIA 驱动，各卡仍按申请值限制显存。工作区可以使用全部获批 CPU 和系统内存。

只要有一张卡申请部分算力，整个工作区就使用独立的 GPU 工作进程和 MPS 服务。工作进程执行固定 SM 分区和显存限制，占用任务获批 CPU 和系统内存的四分之一。工作区通过 Pod 回环接口发送 CUDA 和 NCCL 调用，故障恢复仅操作本次分配的服务。随附客户端暂不支持 NVSHMEM 对称显存。嵌套 Docker 容器使用 GPU 目前需要申请原生整卡。

按比例分配算力时，K3s 需要能够获取对应的 `ghcr.io/kndy666/gpu-platform-cuda-worker:0.4.0` 镜像。使用镜像站或自建版本时，可设置 `slots.gpu.options.isolated_worker_image`。发布流程为 AMD64 和 ARM64 构建镜像。本地构建可使用[工作进程 Dockerfile](../../modules/gpu/nvidia-mps/src/gpu_platform_gpu_nvidia_mps/images/Dockerfile) 所在目录作为构建上下文。

## 编写自己的实现

例如，学校或企业可以实现认证插槽来验证已有账号；使用其他存储系统的部署可以实现存储插槽，向运行模块提供可接收的卷交接物。

1. 实现 [contracts/slots](../../packages/contracts/src/gpu_platform_contracts/slots) 中所选接口的全部职责。[本地认证模块](../../modules/auth/local/src/gpu_platform_auth_local/module.py)是一个简短、完整的例子。
2. 通过 `ModuleDescriptor` 声明插槽、版本、交接物形式、资源和事件。用 `Options(Contract)` 与 `options_model` 校验参数，在 `descriptor.parameters` 发布对应 schema。
3. 使用 Python entry point 注册实现：

```toml
[project]
name = "gpu-platform-auth-directory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gpu-platform-contracts==0.4.0"]

[project.entry-points."gpu_platform.slots.auth"]
directory = "directory_auth.module:Module"
```

依赖公共定义包与实现本身所需的库。英文标签和事件通过 `LocalizedText.zh_hans` 提供简体中文翻译。认证返回经过验证的稳定提供方与身份标识，控制中心管理平台账号与权限。

将扩展包安装到派生的控制中心或节点镜像，以及主机安装器环境。将镜像标签填入 `cluster.control_image` 或 `cluster.node_image`，再选择 entry point 名称：

```yaml
control:
  auth:
    module: directory
    options:
      issuer: https://identity.example.edu
```

部署前校验完整配置。安装器检查必须插槽、版本、交接物形式、功能依赖和主机条件。

## 让模块稳定配合

通过 `ModuleContext` 取得节点身份、参数和模块专用状态目录。资源使用稳定键与单位登记，控制中心据此生成配额和申请。操作使用稳定标识，重试保持幂等。

主机钩子准备系统依赖，安装钩子交换仓库 CA、加速卡运行设置等提供方信息，`compose(context)` 补充服务与挂载。安装按提供方顺序执行，卸载反序执行。使用 `HostOwnership` 记录创建的资源，只撤销模块拥有的变更，并保留用户数据和管理员修改。

公共定义包为十个插槽提供可复用行为测试。在测试中启用 `gpu_platform_contracts.testing.fixtures`，从 `gpu_platform_contracts.testing` 继承对应套件，并提供隔离的实现 fixture。[套件定义](../../packages/contracts/src/gpu_platform_contracts/testing/suites.py)列出了各自的 fixture 契约。

检查资源记账、权限、并发、重试、取消、到期和失败清理。集成到工作区后运行模块测试与 `uv run lint-imports`，其他项目检查见[开发指南](development.md)。
