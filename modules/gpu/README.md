# Accelerators

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `nvidia-mps`, `ascend` · **Slot:** `gpu_platform.slots.gpu`

Choose how a node allocates accelerators. NVIDIA MPS supports shared, whole-card, and multiple-GPU requests using compute percentages and GiB of memory. Ascend supports exclusive whole-chip allocation on ARM64 Ascend 310P hosts.

Install the appropriate driver before setup. NVIDIA requires the MPS commands used by the module; Ascend images need a compatible CANN stack. Both implementations persist allocation leases. CPU-only nodes set `slots.gpu: null`.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/gpu.py) and reusable behavioral suites in `gpu-platform-contracts`.
