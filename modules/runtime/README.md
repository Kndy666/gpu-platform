# Runtime modules

- `k3s`: registered as `gpu_platform.slots.runtime:k3s`.

Runs standard development environments and batch workloads on node-local K3s with Kueue. Host installation supplies LXCFS, security profiles, and bundled environment assets. It accepts Kubernetes PVCs, registry or local images, and optional NVIDIA MPS allocations. Supports Ubuntu 22.04/24.04 on AMD64 or ARM64.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
