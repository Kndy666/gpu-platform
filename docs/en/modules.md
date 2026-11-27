# Module guide

[Home](../../README.md) · [简体中文](../zh/modules.md)

Select modules for each host in `cluster.yaml`. Ten extension slots cover infrastructure choices from authentication to workload execution. Built-in modules and custom third-party modules implement the same contracts.

## Built-in modules

| Slot | Built-in implementations | Responsibility |
| --- | --- | --- |
| [Authentication](../../modules/auth/README.md) | `local` | Account credential verification |
| [Transport](../../modules/transport/README.md) | `local`, `reverse-ssh` | Node commands, workspace relay, and data transfer |
| [Login entry](../../modules/entry/README.md) | `local`, `controller-forward` | External SSH endpoints for dev environments |
| [Runtime](../../modules/runtime/README.md) | `k3s` | Dev environments and batch jobs |
| [Storage](../../modules/storage/README.md) | `rawfile` | Capacity-limited persistent volumes |
| [Registry](../../modules/registry/README.md) | `zot` | Shared and private image storage |
| [Build](../../modules/build/README.md) | `buildkit` | Rootless Dockerfile builds |
| [Accelerator](../../modules/gpu/README.md) | `nvidia-mps`, `ascend` | Shared NVIDIA GPUs or exclusive Ascend 310P chips |
| [Metrics](../../modules/metrics/README.md) | `host` | Timestamped CPU, memory, GPU, disk, and network readings |
| [Notifications](../../modules/notify/README.md) | `inapp`, `pushplus` | In-panel notifications and WeChat PushPlus alerts |

Example host selections for a CPU-only server and an ARM64 Ascend 310P server:

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

Remote hosts also require transport and login-entry settings from [deployment and operations](operations.md). Ascend images need a compatible CANN stack; the Ascend module allocates whole chips via `npu_count` (a 100% compute quota allows one chip, 200% allows two, fractional chips rounded down).

## NVIDIA GPU allocation

- **Whole-card compute (100% on every requested card)**: the container uses the native NVIDIA driver on its reserved devices with per-device memory limits and receives its full CPU and memory allocation. Nested Rootless Docker GPU containers require whole-card compute allocation.
- **Fractional compute (< 100% on any requested card)**: the request uses an isolated GPU worker with a dedicated MPS server enforcing SM partitions and device memory limits, receiving one quarter of the task's CPU and memory budget. CUDA and NCCL calls travel over pod loopback. A failed worker recovers within its own allocation; NVSHMEM symmetric memory is unavailable in fractional mode.

Ensure `ghcr.io/kndy666/gpu-platform-cuda-worker:0.4.0` is reachable by K3s when fractional allocations are used, or point `slots.gpu.options.isolated_worker_image` to your mirror. Build it locally from [modules/gpu/nvidia-mps/src/gpu_platform_gpu_nvidia_mps/images/Dockerfile](../../modules/gpu/nvidia-mps/src/gpu_platform_gpu_nvidia_mps/images/Dockerfile).

## Write a custom module

1. Implement the slot contract in [packages/contracts/src/gpu_platform_contracts/slots](../../packages/contracts/src/gpu_platform_contracts/slots). See [modules/auth/local/src/gpu_platform_auth_local/module.py](../../modules/auth/local/src/gpu_platform_auth_local/module.py) for a concise reference.
2. Declare a `ModuleDescriptor` with slot name, version, handoff forms, resources, events, and a Pydantic `Options(Contract)` model exposed via `descriptor.parameters`.
3. Register the module through a Python entry point:

```toml
[project]
name = "gpu-platform-auth-directory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gpu-platform-contracts==0.4.0"]

[project.entry-points."gpu_platform.slots.auth"]
directory = "directory_auth.module:Module"
```

Provide Simplified Chinese labels via `LocalizedText.zh_hans` for user-facing resource names and events. Install the package into your derived control or node image and the host installer environment, update `cluster.control_image` or `cluster.node_image`, and reference the entry-point name in `cluster.yaml`.

## Contract testing and host ownership

- Use `ModuleContext` for host identity, validated options, and module-scoped state directories.
- Register resources with stable keys and units so quotas and request forms pick them up automatically. Make retries idempotent.
- Track host changes with `HostOwnership` so uninstall removes module-owned setup while leaving user data and operator changes intact.
- Inherit the matching contract test suite from `gpu_platform_contracts.testing` (listed in [packages/contracts/src/gpu_platform_contracts/testing/suites.py](../../packages/contracts/src/gpu_platform_contracts/testing/suites.py)) with isolated fixtures, then verify boundaries with `uv run lint-imports`.
