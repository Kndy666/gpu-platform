# Metrics modules

- `host`: registered as `gpu_platform.slots.metrics:host`.

Samples Linux procfs, sysfs, mounted data pools, and optional NVIDIA readings from the privileged node container. It reports timestamped measurements; the control service handles admission and quotas.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
