# Check CUDA resources and NCCL

[Development guide](../../../../../docs/en/development.md) · [中文指南](../../../../../docs/zh/development.md)

Use an explicitly selected test environment with GPU access. Inside it, compile each probe from this directory:

```bash
cc -O2 -Wall -Wextra cuda_check.c -ldl -o /tmp/cuda_check
cc -O2 -Wall -Wextra gpu_quota.c -ldl -o /tmp/gpu_quota
cc -O2 -Wall -Wextra nccl_check.c -ldl -o /tmp/nccl_check
```

| Probe arguments | Observable check |
| --- | --- |
| `cuda_check EXPECTED_DEVICE_COUNT` | Device visibility, allocation, device write, host read |
| `gpu_quota DEVICE_COUNT SM_COUNTS MEMORY_MIB ALLOCATION_MIB HOLD_SECONDS` | Per-device SM and memory limits |
| `nccl_check DEVICE_COUNT` | All-reduce across one process per visible device |

SM and memory arguments accept comma-separated values. Allocation is per device; zero skips allocation. Run the NCCL probe in an empty temporary directory because it exchanges an identifier through `id.bin`. Set `TEST_CUDART_LIBRARY` when the CUDA runtime has a versioned filename outside the loader search path.

Exit status indicates success or failure. The calling harness supplies GPU allocations and cleans up the task and probe outputs.
