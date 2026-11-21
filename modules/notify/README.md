# Notify modules

- `pushplus`: registered as `gpu_platform.slots.notify:pushplus`.

Delivers core events through the PushPlus HTTPS API. Each receiver supplies a token; encrypted receiver settings belong to the control service. Notifications are disabled in the default configuration.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
