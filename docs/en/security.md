# Identity, execution, and data boundaries

Authentication modules verify credentials and return a provider-qualified subject. The control service owns the resulting account, group membership, administrator role, language preference, SSH keys, and access tokens. The local module uses Django password verification. External modules can return verified identities through the same contract.

## Accounts and connections

Resource quotas and administrator permission are separate account properties. Service operations check ownership for tasks, volumes, images, tokens, and synchronization. Sharing an image grants configured read access. Members can synchronize only their own private images; copies count toward their owner's quota. Administrators synchronize shared images with shared destination copies.

The default environment uses its assigned user identity and public-key SSH. Task pages provide an SSH command and a distinct host-key alias. The login-entry module opens the allocated connection path and closes it when the task ends. Administrators use authorized keys and audited control operations to access managed tasks.

Node transport uses bounded, versioned JSON commands and a separate binary-stream channel. Local transport uses a Unix socket. Reverse SSH transport uses verified host keys, node-specific private keys, and existing SSH tunnels. The SSH account that reaches a node socket has machine-execution authority; protect its keys and group membership accordingly.

| Endpoint | Default | Configuration |
| --- | --- | --- |
| Web and API | TCP 8000 | Host `parameters.web_port`; HTTPS reverse proxy |
| Environment SSH | TCP 30022–30121 | `cluster.ssh_ports` |
| Node RPC | Host Unix socket | State directory and socket group |
| Registry | Node-local TCP 5000 | Registry options, TLS certificate and account credentials |
| K3s API | Host loopback TCP 6443 | Runtime host configuration |

`DJANGO_SECRET_KEY` belongs to the control environment and persisted deployment secrets. Configure trusted origins and secure cookies for the deployed HTTPS origin. Module credentials live in their selected persistent state or configured secret files.

## Development environments

The runtime enforces CPU and memory cgroups and presents quota-aware resource views through LXCFS. It supplies AppArmor and seccomp profiles for nested user namespaces, SSH, and rootless Docker. Runtime-owned profiles and their source notices live under `modules/runtime/k3s/src/gpu_platform_runtime_k3s/host/profiles`.

Personal and working directories are independent storage handoffs. Public datasets are read-only mounts. The runtime disables automatic service-account-token mounting for workloads. Rootless Docker data remains inside the working volume and shares the enclosing task's resource limits.

The NVIDIA module owns GPU discovery, standardized resource quantities, allocation records, MPS processes, and release. Its OCI hook validates the task's allocation at container startup. GPU compute is measured in percentage points of one device; memory uses GiB. The allocation explanation supplied by the module describes hardware-specific rounding and sharing behavior.

The node container is privileged and uses host process and network namespaces to operate its selected infrastructure. Workload identities and module-owned secrets are supplied through explicit handoffs and scoped files. Persistent leases and runtime records support recovery after a node process restart.

## Images, builds, and files

The registry isolates owner repositories, records artifact metadata, and checks ownership for lookup, export, deletion, and import. Export and import use OCI archives; the registry validates archive structure, digests, sizes, and target architecture. TLS trust is configured for runtime pulls and image handoffs.

Rootless BuildKit builds execute with a durable queue, explicit CPU and memory limits, and a timeout. Build credentials and context are scoped to the submitted owner and job. Authenticated base-image pulls use temporary repository-scoped grants. Internal bridge mirrors receive the same scoped credentials and CA; the builder verifies HTTPS on every endpoint and removes job credentials when work ends. A completed build is handed to the registry before the control service publishes the artifact.

The BuildKit host hook installs the named `gpu-platform-buildkit` AppArmor profile and applies it to the builder container. It permits the builder's user namespaces where AppArmor mediates them, while preserving the host-wide restriction. Uninstall removes the profile only when the module created it and its contents remain unchanged.

Directory browsing and synchronization resolve paths under configured roots and reject traversal and unsafe links. Dataset synchronization resumes individual files on the target data disk, verifies their SHA-256 checksums, and replaces selected target subtrees after verification. Image quotas include copies and active reservations across the cluster.

Audit events store a code and parameters. Rendering follows the viewer's language; notifications follow the recipient's language. Backup and restore preserve the database, encryption secret, node keys, registry metadata, volume contents, and lease state together.
