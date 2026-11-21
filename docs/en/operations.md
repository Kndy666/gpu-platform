# Deploy and use GPU Platform

[Home](../../README.md) · [简体中文](../zh/operations.md)

Start with one server. Add execution hosts later using the same cluster file. Platform services run through Docker Compose; the installer prepares the host dependencies required by your modules.

## Install

Use Ubuntu 22.04 or 24.04 on AMD64 or ARM64. Mount your data disks and install the accelerator driver before setup.

From the repository root, copy the configuration and prepare the installer:

```bash
cp config/platform.default.yaml cluster.yaml
sudo ./packages/installer/bootstrap.sh
```

Edit these settings before continuing:

| Setting | What to choose |
| --- | --- |
| `cluster.public_host` | Address users will use to reach the platform |
| `cluster.control_image`, `cluster.node_image` | Available image tags, built locally or published to your registry |
| `hosts.main.architecture` | `amd64` or `arm64` |
| `defaults.slots.storage.options.pools` | Mounted data disk paths |
| `defaults.slots.metrics.options.pools` | The same pools when disk monitoring is wanted |
| `defaults.parameters.datasets` | Public dataset directory |
| `hosts.<name>.slots` | Per-host module choices; set `gpu: null` for CPU-only hosts |

The default configuration includes example image tags. To prepare local images, run these commands on each target architecture and set the corresponding tags in `cluster.yaml`:

```bash
docker build -f docker/control/Dockerfile -t gpu-platform-control:0.4.0 .
docker build -f docker/node/Dockerfile -t gpu-platform-node:0.4.0 .
```

Use an existing Docker installation for these builds, or build and publish from another machine. The installer prepares Docker and Compose on deployment hosts when needed. The repository's CI also supports publishing AMD64 and ARM64 images.

Validate, inspect the plan, and install:

```bash
sudo platformctl validate --config cluster.yaml --host main
sudo platformctl plan --config cluster.yaml --host main
sudo platformctl install --config cluster.yaml --host main
```

The installer checks modules, prepares host infrastructure, initializes the database, and starts services. The default deployment directory is `/var/lib/gpu-platform/deploy/main`; `--output PATH` changes it. Open `http://YOUR_HOST:8000`. Read `PLATFORM_INITIAL_ADMIN_PASSWORD` in the generated root-only `.env`, sign in as `admin`, and change the password. The standard environment image is built automatically once the node's build and registry services are ready.

For an external HTTPS address, put a reverse proxy in front of the web service and set host `parameters.https: true` and `parameters.public_origin` to that origin. `parameters.web_port` changes the default port `8000`.

## Manage with Compose

```bash
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml ps
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml logs --tail 100
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml up -d
```

The default control services are `web`, `loop`, and `links`. An execution host also runs `node` and selected module services. Host-mounted state and data disks persist across container rebuilds. Use `platformctl upgrade` for version and configuration changes.

## Add a node or change modules

The cluster file has four sections: `cluster` for shared addresses and images, `defaults` for modules and features, `control` for authentication and policy, and `hosts` for machine roles and overrides. Mappings merge, lists replace inherited lists, and `null` disables a slot. Keep runtime and storage namespaces aligned.

For example, add this host to your existing file:

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

This example uses an existing reverse SSH listener on the control host. Prepare a separate node key and verified `known_hosts` in the configured directory. The remote SSH account needs access to the node socket; its default group is GID `10001`, configurable with `parameters.socket_group`. Install `compute-a` on that machine with the same cluster file, and apply the updated configuration on the control host through the maintenance workflow below.

For a different accelerator, replace the `gpu` selection with `module: ascend` on a supported ARM64 Ascend 310P host. Its images need a matching CANN stack. The [module guide](modules.md) lists all choices and explains custom implementations.

## Start working

Upload an SSH public key in the panel, choose a node and image, request resources and a time range, then connect using the task page's SSH or VS Code settings. Your enabled keys apply to your environments on every node, including updates to running environments.

| Need | Use |
| --- | --- |
| Develop interactively | An environment with an approved end time; request an extension when needed |
| Run a program | A batch task that records logs and exit status |
| Keep personal files | `/home/<user>`, with a separate home volume on each node |
| Keep task data and Docker state | `/workspace`, with Docker data in `/workspace/.docker` |
| Read shared data | `/datasets`, mounted read-only |
| Reuse a configured environment | Save it as an image, or submit it as the source of a batch task |

Standard environments provide Rootless Docker. On NVIDIA nodes, inner GPU containers can use `docker run --rm --gpus all IMAGE COMMAND` within the outer task's allocation. Work volume and image retention are managed separately; an environment save captures its writable image layer.

The synchronization page shows progress for image and public-directory copies. Members synchronize their own private images; administrators also synchronize shared images and public directories. Image copies require matching CPU architectures; private copies consume their owner's image quota. Public data supports manual and periodic synchronization, bandwidth limits, checksum verification, and interrupted-file resume. Personal and work volumes stay on their node.

## Upgrade, back up, or uninstall

Put all execution nodes into maintenance mode and finish or stop active and queued tasks. On the control host:

```bash
sudo platformctl upgrade --config cluster.yaml --host main
sudo platformctl maintenance-report --config cluster.yaml --host main \
  --output /tmp/platform-maintenance.json
```

Copy the protected report to the worker through your administrative connection, then run there:

```bash
sudo platformctl upgrade --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

Reports must cover every configured node, match the cluster configuration, and be no older than 60 seconds. Export a fresh bundle for each worker and after long image downloads. Remove the copied report after use. A failed readiness check stops the operation before services shut down.

Back up the database, `.env`, node keys, registry state, allocation records, and data volumes together. Uninstall uses the same maintenance checks: replace `upgrade` with `uninstall` in the commands above. It stops Compose and removes module-owned components while retaining user data and pre-existing resources. New databases create the current schema directly.

<details>
<summary>Additional deployment settings</summary>

- **Registry networking:** Zot defaults to `https://127.0.0.1:5000`. BuildKit uses the internal `registry:<port>` mirror with the same credentials and CA. For a DNS address, set registry `publish_address` to the intended interface. Use `build_mirrors` for custom builder networks. External registry CAs belong in runtime `registry_trust`; relative paths resolve under the state directory.
- **Default environment recipe:** `parameters.bootstrap_recipe` selects a controller-readable Dockerfile, and `parameters.bootstrap_context` selects a builder context such as `trusted:environment`. The default combined host publishes the bundled recipe automatically. A dedicated control host needs a readable recipe. The control management command `bootstrap_workspace` can enqueue a build explicitly.
- **Quotas:** Configure module resource keys under group `resources`. `home_size_gib` is the default for new members; members retain their own settings. Work volume quotas apply to the capacity requested per container. NVIDIA memory requests use GiB in increments of 1 MiB. Maximum requests resolve against device budgets and quotas at submission, and retain the resolved quantities while queued.
- **Offline hosts:** Supply image archives through `parameters.image_archives`, enable `parameters.offline`, and prepare system packages and the runtime's versioned download cache.
- **Firewall:** `parameters.network` selects public interfaces and private ports. The installer-owned firewall service reapplies its rules after reboot.

Periodic public-data synchronization goes under `control.sync`:

```yaml
control:
  sync:
    datasets:
      - source: main
        targets: [compute-a]
        paths: [reference]
        interval_seconds: 3600
        bandwidth_bytes_per_second: 10485760
    images: []
```

Public-directory policies apply source changes and removals to selected target subtrees. Image policies use `image`, `source`, `targets`, and an optional bandwidth limit.

</details>
