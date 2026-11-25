# Adapt the platform with modules

[Home](../../README.md) · [简体中文](../zh/modules.md)

Pick modules for each host in `cluster.yaml`. Ten slots cover infrastructure choices, from sign-in to workload execution. You can use the supplied implementations or package your own.

## Choose a starting point

| Slot | Supplied implementations | Use it for |
| --- | --- | --- |
| [Authentication](../../modules/auth/README.md) | `local` | Platform account sign-in |
| [Transport](../../modules/transport/README.md) | `local`, `reverse-ssh` | Node commands, connections, and data transfer |
| [Login entry](../../modules/entry/README.md) | `local`, `controller-forward` | SSH connections to environments |
| [Runtime](../../modules/runtime/README.md) | `k3s` | Development environments and batch jobs |
| [Storage](../../modules/storage/README.md) | `rawfile` | Persistent volumes with capacity limits |
| [Registry](../../modules/registry/README.md) | `zot` | Private and shared images |
| [Build](../../modules/build/README.md) | `buildkit` | Dockerfile builds |
| [Accelerator](../../modules/gpu/README.md) | `nvidia-mps`, `ascend` | Shared NVIDIA GPUs or exclusive Ascend 310P chips |
| [Metrics](../../modules/metrics/README.md) | `host` | Timestamped hardware measurements |
| [Notifications](../../modules/notify/README.md) | `pushplus` | User event delivery; disabled by default |

For a CPU-only server, disable the accelerator slot. On an ARM64 Ascend 310P server with installed drivers, select Ascend instead:

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

Merge these selections into your host definitions; remote hosts also need transport and login-entry settings from the [deployment guide](operations.md). Ascend images need a compatible CANN stack. The module assigns whole chips through `npu_count`; a per-device compute quota of 100% permits one chip, 200% permits two, with fractional chips rounded down.

## NVIDIA GPU allocation

When every requested card has a 100% compute allocation, the workspace uses the native NVIDIA driver on its reserved devices. Each device keeps its requested memory limit. The workspace receives its full CPU and system memory allocation.

If any card has a fractional compute allocation, the entire request uses a private GPU worker and a dedicated MPS server. The worker enforces fixed SM partitions and device memory limits. It receives one quarter of the task's CPU and system memory. Workspace clients send CUDA and NCCL calls over pod loopback. A failed worker recovers within its own allocation. The bundled client lacks NVSHMEM symmetric memory support. GPU access from nested Docker containers currently requires a native allocation.

Make the matching `ghcr.io/kndy666/gpu-platform-cuda-worker:0.4.0` image available to K3s for fractional allocations. Set `slots.gpu.options.isolated_worker_image` to use a mirror or another build. The release workflow builds the worker for AMD64 and ARM64. For local builds, use the directory containing the [worker Dockerfile](../../modules/gpu/nvidia-mps/src/gpu_platform_gpu_nvidia_mps/images/Dockerfile) as the build context.

## Add your own implementation

For example, an organization can implement the authentication slot to verify its existing accounts. A site with another storage backend can implement the storage slot and provide volume handoffs accepted by its runtime.

1. Implement every responsibility of the chosen interface in [contracts/slots](../../packages/contracts/src/gpu_platform_contracts/slots). The [local authentication module](../../modules/auth/local/src/gpu_platform_auth_local/module.py) is a small complete example.
2. Declare a `ModuleDescriptor` with the slot, version, supported handoff forms, resources, and events. Use `Options(Contract)` and `options_model` for validated options; publish its schema in `descriptor.parameters`.
3. Register the implementation through a Python entry point:

```toml
[project]
name = "gpu-platform-auth-directory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gpu-platform-contracts==0.4.0"]

[project.entry-points."gpu_platform.slots.auth"]
directory = "directory_auth.module:Module"
```

Depend on contracts and your implementation libraries. English labels and events have Simplified Chinese translations through `LocalizedText.zh_hans`. Authentication returns a verified, stable provider/subject identity; control manages the platform account and permissions.

Install the package into the derived control or node image and the host installer environment. Set the image tag in `cluster.control_image` or `cluster.node_image`, then select the entry-point name:

```yaml
control:
  auth:
    module: directory
    options:
      issuer: https://identity.example.edu
```

Validate the complete configuration before deployment. The installer checks required slots, versions, handoff forms, feature dependencies, and host requirements.

## Keep integration predictable

Use `ModuleContext` for node identity, options, and a module-specific state directory. Register resources by stable key and unit so control can include them in quotas and requests. Use stable operation identifiers and make retries idempotent.

Host hooks prepare system dependencies; installation hooks exchange provider information, such as registry CA trust and accelerator runtime settings. `compose(context)` contributes service and mount configuration. Installation follows provider order and removal reverses it. Use `HostOwnership` to track created resources and undo only owned changes while preserving user data and operator modifications.

The contracts package provides reusable behavioral suites for all ten slots. In your tests, enable `gpu_platform_contracts.testing.fixtures`, inherit the appropriate suite from `gpu_platform_contracts.testing`, and supply isolated implementation fixtures. [Suite definitions](../../packages/contracts/src/gpu_platform_contracts/testing/suites.py) list each fixture contract.

Check resource accounting, permissions, concurrency, retries, cancellation, expiry, and failure cleanup. Run your package tests and `uv run lint-imports` after workspace integration. Further project checks are in [development](development.md).
