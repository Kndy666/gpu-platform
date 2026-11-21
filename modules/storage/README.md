# Storage modules

- `rawfile`: registered as `gpu_platform.slots.storage:rawfile`.

Uses OpenEBS Rawfile on K3s with mounted data pools. It creates quota-limited PVCs, expands volumes, copies idle volumes, and shrinks through a durable copy-and-switch operation. Deletion waits for underlying volume reclamation. This implementation requires the K3s runtime.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
