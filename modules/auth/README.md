# Auth modules

- `local`: registered as `gpu_platform.slots.auth:local`.

The local implementation runs in the control image and validates accounts with Django password hashing. It returns a stable provider and subject identity; the control service creates user records and assigns the default group.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
