# Environment login

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `local`, `controller-forward` · **Slot:** `gpu_platform.slots.entry`

Give each running environment an SSH connection address. `local` exposes the node-local path; `controller-forward` connects through the control host using the selected transport.

The platform allocates public ports centrally and closes listeners and connections when the task ends.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/entry.py) and reusable behavioral suites in `gpu-platform-contracts`.
