# Image storage

[Module guide](../../docs/en/modules.md) · [中文指南](../../docs/zh/modules.md) · [Deployment](../../docs/en/operations.md)

**Implementations:** `zot` · **Slot:** `gpu_platform.slots.registry`

Keep private and shared OCI images in a node-local Zot registry managed by Compose. Owner-scoped repositories support image lookup, deletion, validated export and import, and temporary read access for builds.

The default endpoint is `https://127.0.0.1:5000`; BuildKit uses the internal `registry:<port>` mirror. The installer shares TLS trust with the runtime. Address and custom-network settings are covered in the deployment guide.

Custom implementations use the [slot interfaces](../../packages/contracts/src/gpu_platform_contracts/slots/registry.py) and reusable behavioral suites in `gpu-platform-contracts`.
