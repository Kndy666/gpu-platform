# Deployment and operations

[Home](../../README.md) · [简体中文](../zh/operations.md) · [Configuration](configuration.md)

Start with one server and add execution servers later using the same cluster file. Platform services run through Docker Compose, while the installer prepares host dependencies required by your selected modules.

## Install

Use Ubuntu 22.04 or 24.04 on AMD64 or ARM64. Mount your data disks and install the driver for your accelerator before setup.

From the repository root, copy the default configuration and install `platformctl`:

```bash
cp config/platform.default.yaml cluster.yaml
sudo ./packages/installer/bootstrap.sh
```

Edit these settings in `cluster.yaml` before continuing:

| Setting | What to choose |
| --- | --- |
| `cluster.public_host` | Hostname or IP users open in the browser and use for SSH |
| `cluster.control_image`, `cluster.panel_image`, `cluster.node_image` | Image tags built locally or published to your registry |
| `hosts.main.architecture` | `amd64` or `arm64` |
| `defaults.slots.storage.options.pools` | Mounted data disk paths |
| `defaults.slots.metrics.options.pools` | Matching paths when disk monitoring is enabled |
| `defaults.parameters.datasets` | Shared datasets directory |
| `hosts.<name>.slots` | Per-host module choices; set `gpu: null` for CPU-only servers |

To build images locally on each target architecture and match the tags in `cluster.yaml`:

```bash
docker build -f docker/control/Dockerfile -t gpu-platform-control:0.4.0 .
docker build -f docker/panel/Dockerfile -t gpu-platform-panel:0.4.0 .
docker build -f docker/node/Dockerfile -t gpu-platform-node:0.4.0 .
```

Validate, review the plan, and install:

```bash
sudo platformctl validate --config cluster.yaml --host main
sudo platformctl plan --config cluster.yaml --host main
sudo platformctl install --config cluster.yaml --host main
```

The installer verifies module compatibility, prepares host infrastructure, initializes the database, and starts Docker Compose services. The default deployment directory is `/var/lib/gpu-platform/deploy/main` (override with `--output PATH`).

Open `http://YOUR_HOST:8000`, read `PLATFORM_INITIAL_ADMIN_PASSWORD` from the generated root-only `.env`, sign in as `admin`, and change the password. The standard environment image builds automatically once the node's build and registry services are ready.

For HTTPS, place a TLS reverse proxy in front of `panel` and set `parameters.https: true` and `parameters.public_origin` on the control host. See the [configuration reference](configuration.md) for reverse proxy, storage pool, cache, and sync tuning.

## Manage with Compose

```bash
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml ps
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml logs --tail 100
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml up -d
```

Control hosts run `panel` (Nginx serving the Vue SPA and proxying `/api/` and `/healthz` to `web`), `web` (internal API service), `loop`, `links`, and `notifier` when notifications are configured. Execution hosts run `node` and selected module services such as `registry` and `builder`. Host-mounted state and data disks persist across container rebuilds.

## Add a server

Add the remote server to `cluster.yaml`:

```yaml
hosts:
  compute-a:
    roles: [node]
    architecture: arm64
    address: 127.0.0.1
    slots:
      gpu: null
      transport:
        module: reverse-ssh
        options:
          host: 127.0.0.1
          ssh_port: 2201
          ssh_user: gpu-platform
          identity: compute-a
          key_directory: /var/lib/gpu-platform/ssh-keys
      entry:
        module: controller-forward
        options:
          public_host: compute.example.edu
```

Prepare a dedicated node SSH key and verified `known_hosts` in `key_directory`, and grant the remote SSH account access to the node socket group (default GID `10001`). Install `compute-a` with the same `cluster.yaml`, then apply the updated file on the control host using the upgrade procedure below.

On an ARM64 Ascend 310P server, set `slots.gpu.module: ascend` and provide images with a matching CANN stack. See the [module guide](modules.md) for all slot options.

## Monitoring and usage reports

- **Administration → Live load**: real-time CPU, memory, GPU/NPU, disk, and network readings plus compressed metrics history (1-minute samples for 7 days, 15-minute aggregates for 90 days, hourly aggregates for 365 days).
- **Administration → Usage statistics**: current cluster allocation and historical GPU/NPU-hours filtered by date, member, server, and device model, with a copyable text report.
- **Administration → Storage & volumes**: pool free space and user home/work volumes, with deletion for idle volumes.

## Upgrade, back up, or uninstall

Put all execution servers into maintenance mode and wait for active and queued tasks to finish or stop. On the control host:

```bash
sudo platformctl upgrade --config cluster.yaml --host main
sudo platformctl maintenance-report --config cluster.yaml --host main \
  --output /tmp/platform-maintenance.json
```

Copy the maintenance report to each worker server through your administrative connection, then run:

```bash
sudo platformctl upgrade --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

Maintenance reports must cover every configured server, match the cluster file, and be less than 60 seconds old. Generate a fresh report for each worker and remove the copied file afterward. Failed readiness checks stop the command before any service shuts down.

Back up the database, `.env`, node keys, `metrics-history/`, registry state, allocation records, and data volumes together. To uninstall, replace `upgrade` with `uninstall` using the same maintenance report workflow; uninstall stops Compose and removes module-owned host setup while preserving user data and pre-existing resources.
