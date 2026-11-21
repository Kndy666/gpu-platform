# Installation and operations

Use Ubuntu 22.04 or 24.04 on AMD64 or ARM64. Mount the configured data disks. The NVIDIA module checks its driver and MPS prerequisites; install the driver before running the installer. K3s, Kueue, Rawfile, LXCFS, OCI settings, and module services are prepared by module installation hooks.

## Prepare one cluster configuration

```bash
cp config/platform.default.yaml cluster.yaml
```

Set `cluster.public_host`, `hosts.<name>.architecture`, data pool paths, and public dataset paths. Set registry addresses to an address reachable from the host and its workloads. The registry's installation handoff supplies its endpoint and CA file to the runtime. For external registries, configure the runtime's `registry_trust` mapping; relative CA paths resolve under the cluster state directory. The generated registry certificate lives under `registry/tls.crt` in that directory. Zot publishes its advertised port on host loopback by default and shares a private Compose bridge with BuildKit. Build handoffs supply the internal `registry:<port>` mirror while keeping image references unchanged. For a DNS-based advertised URL, set registry `publish_address` to the intended host interface; use `build_mirrors` to override mirror authorities for custom builder networks. The certificate must cover the advertised hostname and mirror hostnames.

The four configuration sections have these responsibilities:

| Section | Contents |
| --- | --- |
| `cluster` | Public host, SSH port pool, default language, control and node images, state directory |
| `defaults` | Slot selections, module options, feature defaults, shared parameters |
| `control` | Authentication, notification modules, initial groups and resource quotas, synchronization policies, database settings |
| `hosts` | Roles, architecture, address, slot overrides, feature overrides, host parameters |

The packaged YAML is also available at `config/platform.default.yaml`. A host override merges mappings; lists replace inherited lists. `null` disables a slot. For a CPU-only host:

```yaml
hosts:
  main:
    roles: [control, node]
    architecture: amd64
    slots:
      gpu: null
```

Keep the configured runtime and storage namespaces aligned. Configure metrics disk paths for the same storage pools when disk metrics are required. Group `resources` mappings use the keys registered by selected modules, such as `cpu`, `memory`, `work_storage`, `image_storage`, `gpu_compute`, and `gpu_memory`. Group `home_size_gib` sets the default home volume capacity for new members. Members can override it, and changing a group default preserves existing member capacities. Each node has a separate home volume; files are not synchronized automatically. Home volumes use the member capacity and physical pool capacity. Work volume quotas limit each container's requested capacity. Capacities use GiB/TiB. The NVIDIA module uses driver-reported total memory, with requests in integer GiB.

Validate before installation:

```bash
uv run platformctl validate --config cluster.yaml --host main
uv run platformctl plan --config cluster.yaml --host main
```

`validate --machine` also runs local machine checks. Plain validation and planning inspect configuration and module declarations.

## Build and install

Build images on a machine with Docker:

```bash
docker build -f docker/control/Dockerfile -t gpu-platform-control:0.4.0 .
docker build -f docker/node/Dockerfile -t gpu-platform-node:0.4.0 .
```

Set `cluster.control_image` and `cluster.node_image` to those tags or to tags in your image registry. The supplied GitHub Actions workflow builds AMD64 and ARM64 images. Publish a version before referencing its registry tag on deployment hosts.

Prepare the host-side Python environment, then install the selected host:

```bash
sudo ./packages/installer/bootstrap.sh
sudo platformctl install --config cluster.yaml --host main
```

The installer prepares pinned Docker Engine 29.1.3 and Compose on new hosts. Existing installations need Docker API 1.44 or later and the Compose plugin. The default private ports are 6443 and 10250. Configured private-port firewall rules are owned by `gpu-platform-firewall.service`; `public_interface: auto` selects the lowest-metric default-route interface, and the unit reapplies rules after reboot. It runs module checks and hooks, writes resolved configuration and a secret environment file, prepares container images, creates the current database schema, initializes defaults, and starts Compose services. The generated directory defaults to `/var/lib/gpu-platform/deploy/main`; `--output PATH` changes it.

Use the generated Compose file for routine inspection:

```bash
sudo docker compose --project-directory /var/lib/gpu-platform/deploy/main \
  -f /var/lib/gpu-platform/deploy/main/compose.yaml ps
sudo docker compose --project-directory /var/lib/gpu-platform/deploy/main \
  -f /var/lib/gpu-platform/deploy/main/compose.yaml logs --tail 100 web loop node
```

The default web port is `8000`, configured by `hosts.<name>.parameters.web_port`. For HTTPS, set host `parameters.https: true` and `parameters.public_origin` to the external origin, then place an HTTPS reverse proxy in front of the web service. Compose derives secure-cookie and trusted-origin settings from these values. Additional control environment variables belong in `parameters.environment`. The generated `.env` stores `DJANGO_SECRET_KEY` and the initial administrator password (`PLATFORM_INITIAL_ADMIN_PASSWORD`). Read the root-only file locally to sign in as `admin`, then change the password. Preserve the Django key with database backups. Existing accounts are retained when bootstrap runs.

The default combined control/node host publishes the bundled environment recipe at `bootstrap/environment/Dockerfile` under the state directory. The control loop queues the default image build after the node reports its build and registry capabilities and an administrator exists. Workers use the same configured recipe and their own `build.options.trusted_contexts.environment` directory. A dedicated control host or custom runtime must store a readable recipe on the control host. Each target node's `parameters.bootstrap_recipe` (or inherited `defaults.parameters.bootstrap_recipe`) selects that controller-side file, using an absolute path or a path relative to the controller state directory. Set the target's `parameters.bootstrap_context` to a matching `trusted:<name>` context configured in its builder. A missing recipe skips automatic initialization. To enqueue explicitly, run `python -m gpu_platform_control.manage bootstrap_workspace --all-nodes --recipe PATH --context trusted:environment --wait 0` inside the web service.

## Add execution hosts

List every machine in the same file. The control host can also execute tasks. Remote nodes select transport and entry modules explicitly:

```yaml
hosts:
  main:
    roles: [control, node]
    architecture: amd64
  compute-a:
    roles: [node]
    architecture: arm64
    address: 127.0.0.1
    slots:
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

This example uses an existing reverse SSH listener on the control host. Give each node its own key and verified `known_hosts` entry. Store key files in the configured key directory. The remote SSH account needs access to the node socket, whose default group is numeric GID `10001`; set `parameters.socket_group` for a different local group. Run `platformctl install --host compute-a` on that machine using the same YAML.

The control host allocates user SSH ports and remote login modules open the connection path. Public datasets, image repositories, and volume pools remain attached to their respective nodes.

## Work with environments and images

Add an SSH public key, choose a node and image, set resources and a time range, then submit an environment request. The task page provides SSH and VS Code connection settings. Development environments stop at their approved end time. Extension requests follow the account's approval policy and extension allowance.

Batch jobs run their command until it exits. Exit code and logs appear with the task. `/workspace` holds task data, `/home/<user>` holds personal data, and `/datasets` exposes configured public data read-only. Docker data belongs to the working volume.

Saving an environment captures its writable image layer. Personal and working volumes have separate lifecycles. A job can use an image or an existing environment; the latter workflow prepares the environment snapshot and associated workspace before dispatch.

Synchronization is available from the main navigation. Members can synchronize only their own private images and see only their own jobs. Private copies count toward their owner's image quota. Administrators see all jobs, synchronize shared images with shared destination copies, and can trigger public-directory synchronization. The page shows source, target, transferred bytes, state, and completion time. Public data is sent one file at a time through the selected node transport. Interrupted files resume from their saved byte offset on the destination data disk. Each completed file is checked with SHA-256 before the selected directory is committed; unchanged files are reused locally. Periodic policies live under `control.sync`:

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

Image policies specify `image`, `source`, `targets`, and an optional bandwidth limit. Configure image synchronization between nodes of the same architecture. Public-directory policies apply source changes, including removals, to the selected target subtrees.

## Upgrade and uninstall

Put every execution node into maintenance mode through the node management page. Let active and queued tasks finish or stop them. Upgrade verifies current reports, prepares images, checks maintenance again, then stops services. When host configuration, module versions, or image selections change, it reapplies host and module installation hooks before validating the current database schema and restarting Compose.

On the control host:

```bash
sudo platformctl upgrade --config cluster.yaml --host main
sudo platformctl maintenance-report --config cluster.yaml --host main \
  --output /tmp/platform-maintenance.json
```

Copy the protected report file to the worker through the existing administrative connection, then run on that worker:

```bash
sudo platformctl upgrade --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

The bundle must match the complete cluster configuration and include every configured node, with compatible reports no older than 60 seconds. The worker also queries its own local daemon immediately before shutdown. Export a fresh bundle for each worker; regenerate it if image downloads outlast its validity. Missing, stale, mismatched, busy, or non-maintenance reports stop the upgrade before service shutdown. Remove the copied operational report after use.

`initdb` creates the current schema directly for a new database and validates tables and columns in an initialized database. This refactor targets fresh deployments and includes no application migration history or legacy conversion tool.

For disconnected hosts, transfer Docker image archives and module infrastructure caches. Configure `parameters.image_archives` with absolute archive paths and `parameters.offline: true`. The installer loads those archives and verifies required images are present. Prepare system packages and the runtime's versioned download cache before offline installation.

```bash
sudo platformctl uninstall --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

Uninstall requires maintenance and no active tasks, with a fresh report bundle for a worker. It stops Compose, removes selected module components in reverse order, and retains persisted data for explicit administration. Back up the database, `.env`, node keys, registry state, and data volumes together before storage maintenance.

Runtime and GPU hooks journal the host files, cluster objects, and services they create. Uninstall removes those owned components, restores the module's Docker and registry-trust configuration entries, and preserves unrelated configuration and pre-existing infrastructure. An operator-modified generated file is retained. Module-owned MPS shutdown restores the GPU compute modes recorded before startup. K3s data, workload namespaces, user volumes, and installed system packages remain on disk; a newly created K3s or LXCFS service is disabled. Retained data can be reattached during a later installation.
