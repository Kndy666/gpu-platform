# Persistent storage

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `rawfile` · **Slot:** `gpu_platform.slots.storage`

Keep personal and work data in capacity-limited volumes backed by mounted disk pools. Rawfile supports expansion, idle-volume copying, and shrink through copy-and-switch. Deletion waits for underlying reclamation.

This implementation uses OpenEBS Rawfile on K3s. Keep its namespace aligned with the runtime and mount the configured data pools before installation.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/storage.py) and reusable behavioral suites in `gpu-platform-contracts`.
