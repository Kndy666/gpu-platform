# Transport modules

- `local`: registered as `gpu_platform.slots.transport:local`.
- `reverse-ssh`: registered as `gpu_platform.slots.transport:reverse-ssh`.

The local implementation uses a Unix socket. Reverse SSH connects remote nodes through authenticated SSH and a dedicated links service. Both provide versioned commands, separate bounded data streams, and bidirectional port connections.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
