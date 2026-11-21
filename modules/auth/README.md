# Authentication

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `local` · **Slot:** `gpu_platform.slots.auth`

Use platform accounts for sign-in. The local implementation verifies Django password hashes and returns a stable identity; control manages membership, permissions, and default group assignment.

Implement this slot to connect an organization identity provider.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/auth.py) and reusable behavioral suites in `gpu-platform-contracts`.
