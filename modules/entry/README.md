# Entry modules

- `local`: registered as `gpu_platform.slots.entry:local`.
- `controller-forward`: registered as `gpu_platform.slots.entry:controller-forward`.

The local implementation publishes a task's node-local connection address. Controller forwarding reserves a public login port and connects through the selected transport. Listeners and active connections close when a task ends.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
