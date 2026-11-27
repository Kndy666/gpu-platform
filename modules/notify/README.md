# Notifications

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `inapp`, `pushplus` · **Slot:** `gpu_platform.slots.notify`

Receive task events and administrator announcements in the panel. The example configuration enables `inapp`. To use it with PushPlus, list both implementations on the control host:

```yaml
control:
  notify:
    - module: inapp
    - module: pushplus
```

Use **Administration → Notifications** to send in-app messages, choose automatic events, and review delivery and reading status. Configure the shared WeChat receiver under **Automatic notifications**. Administrators receive WeChat messages through that receiver. The control service encrypts the receiver token.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/notify.py) and reusable behavioral suites in `gpu-platform-contracts`.
