# Image builds

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `buildkit` · **Slot:** `gpu_platform.slots.build`

Build images from Dockerfiles with Rootless BuildKit. Builds have a persistent queue, CPU and memory limits, cancellation, timeouts, and logs. Results enter the selected registry as OCI artifacts.

Trusted bootstrap contexts use `options.trusted_contexts`. Private base images use temporary read credentials and verified TLS, including internal registry mirrors.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/build.py) and reusable behavioral suites in `gpu-platform-contracts`.
