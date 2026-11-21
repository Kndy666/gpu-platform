# Notifications

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `pushplus` · **Slot:** `gpu_platform.slots.notify`

Deliver task and platform events through PushPlus. Notifications are disabled by default and can be enabled under `control.notify`.

Each recipient provides a token. Control encrypts receiver settings and renders messages in the recipient’s language. Multiple notification modules can be selected.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/notify.py) and reusable behavioral suites in `gpu-platform-contracts`.
