# Hardware metrics

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `host` · **Slot:** `gpu_platform.slots.metrics`

View timestamped CPU, memory, disk, network, and available accelerator readings in the panel. The module samples host procfs, sysfs, configured data pools, and optional NVIDIA data.

Set pool paths to match your storage disks. Missing readings remain null; control handles admission and quotas.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/metrics.py) and reusable behavioral suites in `gpu-platform-contracts`.
