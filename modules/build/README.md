# Build slot

The `buildkit` implementation runs rootless BuildKit with a persistent build queue, CPU and memory limits, cancellation, and build logs. It produces OCI archives consumed by a registry module. Select it with `slots.build.module: buildkit`.

Trusted bootstrap contexts are configured with `options.trusted_contexts`; user requests supply their own Dockerfile and allowed remote context. The interface and portable suite live in `gpu_platform_contracts.slots.build` and `gpu_platform_contracts.testing.BuildContract`.

For authenticated base images, `Image.metadata.registry_mirrors` may list internal registry authorities such as `registry:5000`. The builder keeps the submitted `FROM` reference and configures these mirrors with the handoff's scoped read credentials and CA certificate. It verifies TLS for the advertised endpoint and every mirror, and removes job credentials after completion or cancellation.
