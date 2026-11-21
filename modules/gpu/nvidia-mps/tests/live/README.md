# CUDA and NCCL probes

These probes run inside an explicitly selected test environment with GPU access. They do not discover or modify a deployment. Compile them inside that environment with `cc -O2 -Wall -Wextra SOURCE.c -ldl -o /tmp/PROBE`.

- `cuda_check EXPECTED_DEVICE_COUNT` verifies device visibility, allocation, a device write, and a host read.
- `gpu_quota DEVICE_COUNT SM_COUNTS MEMORY_MIB ALLOCATION_MIB HOLD_SECONDS` checks visible SM and memory limits for each device. SM and memory arguments accept comma-separated values. Allocation is per device; use zero to skip allocation.
- `nccl_check DEVICE_COUNT` performs an all-reduce in one process per visible device. Run in an empty temporary directory because the probe exchanges its NCCL identifier through `id.bin`. Set `TEST_CUDART_LIBRARY` if the CUDA runtime library has a versioned filename outside the loader search path.

Probe exit status reports success or failure. GPU allocation and task cleanup belong to the test harness invoking them.
