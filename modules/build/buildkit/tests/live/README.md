# Check image builds and registry access

[Development guide](../../../../../docs/en/development.md) · [中文指南](../../../../../docs/zh/development.md)

Run on a test host with Docker access, from the repository root:

```bash
uv run python modules/build/buildkit/tests/live/smoke.py --enable-docker
```

The check creates a temporary builder, registry, network, cache volume, and state files, then removes them on exit. It verifies queued builds, CPU and memory limits, OCI import, private base-image pulls through a verified TLS bridge mirror, anonymous-access denial, and credential revocation.

Use `--base-image`, `--builder-image`, and `--registry-image` to select test images or approved mirrors. To also build the standard environment, add:

```bash
uv run python modules/build/buildkit/tests/live/smoke.py --enable-docker \
  --environment-context modules/runtime/k3s/src/gpu_platform_runtime_k3s/images \
  --environment-output /tmp/test-standard-environment.tar
```

`--environment-base` and `--apt-mirror` customize that build. The requested output archive is retained for a separate runtime check; remove it when finished. Inputs come from these explicit arguments, independently of platform configuration or databases.
