# Registry modules

- `zot`: registered as `gpu_platform.slots.registry:zot`.

Runs Zot as a Compose service independently of the task runtime. It stores OCI images in owner namespaces with per-image read credentials, streams validated exports and imports, and grants temporary read access for builds. Configure a TLS address reachable from workloads and matching runtime trust.

The registry shares the host's Compose bridge with the builder. Its advertised `url` defaults to `https://127.0.0.1:5000`, published on host loopback for the node and container runtime. The `build_mirrors` option (default `registry:<port>`) populates `registry_mirrors` in build handoff metadata so builds reach that service directly while retaining the advertised image reference. The generated certificate covers both addresses. For a DNS-based advertised URL, set `publish_address` to the intended host interface; it defaults to loopback. `port` selects the registry's container port, and the URL selects the published host port. A custom builder topology can supply its own mirror authorities or an empty list when the advertised address is directly reachable.

The public interface and reusable behavioral suite are provided by `gpu-platform-contracts`. Third-party implementations declare their supported handoff forms and provide the same slot responsibilities.
