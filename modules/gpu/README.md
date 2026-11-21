# Gpu modules

- `nvidia-mps`: registered as `gpu_platform.slots.gpu:nvidia-mps`.

Uses NVIDIA MPS partitions on hardware and drivers that support the required MPS commands. Compute is expressed as a percentage of one GPU and memory in GiB. It supports shared, whole-card, and multi-card requests, persists allocation leases, and installs the container toolkit and memory enforcement hook. Administrators install the GPU driver before setup.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
