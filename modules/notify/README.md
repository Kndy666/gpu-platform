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

Use **Administration → Notifications** to send in-app messages, choose events, and review delivery and reading status. In **Reminder settings**, enter the shared PushPlus token and select the events to send to its WeChat account. Clear the WeChat event selection to stop sending reminders. The control service encrypts the token.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/notify.py) and reusable behavioral suites in `gpu-platform-contracts`.
