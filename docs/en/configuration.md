# Configuration reference

[Home](../../README.md) · [简体中文](../zh/configuration.md) · [Deployment](operations.md)

One YAML file (`cluster.yaml`) describes the entire cluster across four top-level sections: `cluster`, `defaults`, `control`, and `hosts`. Mappings merge from `defaults` into each host, lists replace inherited lists, and setting a slot to `null` disables it on that host.

## Cluster images and network

| Key | Purpose |
| --- | --- |
| `cluster.public_host` | Hostname or IP address users open in the browser and use for SSH |
| `cluster.control_image` | Control API, reconciler, links, and notifier container image |
| `cluster.panel_image` | Standalone Vue 3 + Nginx web panel container image |
| `cluster.node_image` | Execution node daemon image |
| `cluster.ssh_ports` | TCP port range `[start, end]` published for dev environment SSH |
| `cluster.state_directory` | Host state path for the database, keys, compose files, and runtime metadata |

### HTTPS and web port

Set `hosts.<name>.parameters.web_port` to change the port published by the `panel` container (default `8000`). When placing an external TLS reverse proxy in front of `panel`:

```yaml
hosts:
  main:
    parameters:
      web_port: 8000
      https: true
      public_origin: https://gpu.example.edu
```

Forward `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto: https` from the external reverse proxy to the `panel` container. `panel` proxies `/api/` and `/healthz` to the internal `web` service over the Compose network.

## Storage pools and image pools

- **Storage pools**: set `defaults.slots.storage.options.pools` (or per-host overrides) to mounted data disk paths for user home and work volumes. Mirror those paths under `slots.metrics.options.pools` to monitor disk usage on **Live load**.
- **Image pools**: set `hosts.<node>.slots.registry.options.pools` to local pool names and absolute data-disk directories on separate mounted filesystems.
  - The default Zot route uses the `existing` directory on `existing_images_pool` (the first pool by default). Prepare existing registry data in that directory during a maintenance window with the registry stopped, preserving directory layout, ownership, and hard links.
  - New images select the pool with enough free space and the lowest share of that filesystem occupied by the user's images. Keep existing pool names and mounts intact for assigned images.

## Registry networking and default environment recipe

- **Registry networking**: Zot defaults to `https://127.0.0.1:5000`. BuildKit uses the internal `registry:<port>` mirror with the same credentials and CA. Set registry `publish_address` for a custom interface, `build_mirrors` for custom builder networks, and runtime `registry_trust` for external registry CAs (relative paths resolve under `cluster.state_directory`).
- **Default environment recipe**: `parameters.bootstrap_recipe` selects a controller-readable Dockerfile and `parameters.bootstrap_context` selects a builder context such as `trusted:environment`. Combined control+node hosts publish the bundled recipe automatically; dedicated control hosts need a readable recipe path. Run `python -m gpu_platform_control.manage bootstrap_workspace` to enqueue a bootstrap build manually.

## Cache budgets and cleanup

- **BuildKit cache**: targets 12 GiB and retains unused entries for 4 hours by default. Override per host with `slots.build.options.cache_budget_gib` and `cache_max_age_hours`. Pruning runs after builds finish and every 15 minutes while idle.
- **K3s image cache**: reclaims unused images after 4 hours (`slots.runtime.options.image_cache_max_age_hours`). Kubelet starts space-based collection at 85% disk usage, targeting 80%.
- **Host Docker build cache**: an installer-managed timer runs every 15 minutes, reclaiming unused Docker build cache older than 4 hours with a 4 GiB cap.
- **Temporary archives**: build and import staging files are removed once the result is stored or fails permanently. Orphaned staging files become eligible for cleanup after 4 hours.

## Quotas and system storage

- **System storage**: set the per-user ephemeral storage budget in the group editor or `control.groups.<group>.resources.system_storage` (default `5` GiB across all servers). Each task requests whole GiB, mapped to the K3s Pod ephemeral-storage request and limit (writable layer and container logs). Reservations release after task cleanup.
- **Group and member quotas**: configure slot resource keys under `control.groups.<group>.resources`. `home_size_gib` sets the default home volume limit for new members; existing members retain their own settings. Work volume quotas apply per container. NVIDIA memory requests accept GiB in 1 MiB increments. Requests specifying `"max"` resolve against remaining quota and physical capacity at submission.

## Metrics history archive

The control service records compressed metrics history under `<state_directory>/metrics-history/`:

- 1-minute samples retained for 7 days
- 15-minute aggregates retained for 90 days
- Hourly aggregates retained for 365 days

Default storage limits are a 512 MiB archive cap and a 2048 MiB free-space reserve. Override in `cluster.yaml`:

```yaml
control:
  metrics_history:
    max_mib: 512
    reserve_mib: 2048
```

Recording pauses automatically when free space drops below the reserve and resumes once space recovers.

## Periodic synchronization

Configure scheduled dataset or image synchronization under `control.sync`:

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

Dataset sync applies source changes and file removals to the selected subtrees. Image policies use `image`, `source`, `targets`, and an optional bandwidth limit.

## Offline deployments, socket access, and firewall

- **Offline hosts**: provide image archives via `parameters.image_archives`, set `parameters.offline: true`, and prepare local system packages and the runtime's versioned download cache.
- **Node socket group**: `parameters.socket_group` configures the GID allowed to connect to the node daemon Unix socket (default `10001`).
- **Workspace SSH**: enabled by default on supported nodes (`local` and `reverse-ssh` transports). Disable per host with `features.workspace_ssh: false`.
- **Firewall**: `parameters.network` defines public interfaces and private ports. The installer-owned firewall service reapplies rules on boot.
