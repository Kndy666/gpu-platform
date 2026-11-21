# Isolated Docker build checks

Run `smoke.py --enable-docker` with the workspace Python environment on a test host with Docker access. The script creates temporary containers, a network, a cache volume and state files, then removes them on exit. It verifies a durable serial queue, enforced Docker CPU/memory limits, OCI archive import, a loopback-advertised private base image pulled through a TLS-verified bridge mirror, anonymous access denial and scoped credential revocation.

```sh
uv run python modules/build/buildkit/tests/live/smoke.py --enable-docker
```

`--base-image`, `--builder-image` and `--registry-image` select test inputs or approved mirrors. To build the bundled standard environment as well, add:

```sh
--environment-context modules/runtime/k3s/src/gpu_platform_runtime_k3s/images \
--environment-output /tmp/test-standard-environment.tar
```

The optional environment build accepts `--environment-base` and `--apt-mirror`. The output path explicitly retains its OCI archive for a separate runtime smoke test. No current platform configuration, database or cluster is read.
