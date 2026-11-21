# Workload runtime

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `k3s` · **Slot:** `gpu_platform.slots.runtime`

Run SSH and VS Code development environments and batch programs on node-local K3s with Kueue. Environments include persistent home and work volumes, Rootless Docker, and resource views matching their CPU and memory limits.

The module prepares K3s, LXCFS, security profiles, and standard environment assets. It accepts Rawfile PVCs, registry or local images, and the selected accelerator handoff. Supported hosts are Ubuntu 22.04/24.04 on AMD64 or ARM64.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/runtime.py) and reusable behavioral suites in `gpu-platform-contracts`.
