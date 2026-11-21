# Node connections

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `local`, `reverse-ssh` · **Slot:** `gpu_platform.slots.transport`

Connect control to execution nodes. `local` uses a Unix socket for a colocated node; `reverse-ssh` reaches remote nodes through an existing authenticated SSH tunnel and the links service.

Both implementations provide versioned commands, bounded binary streams, and bidirectional port connections. Remote nodes need a separate key, verified host keys, and an SSH account with node-socket access.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/transport.py) and reusable behavioral suites in `gpu-platform-contracts`.
