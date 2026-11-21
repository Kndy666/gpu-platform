# Access and data boundaries

[Home](../../README.md) · [简体中文](../zh/security.md)

Platform accounts connect resource ownership, quotas, public keys, and access tokens. Authentication modules verify identity; control checks permissions for each operation.

## Accounts and SSH

Members manage their own tasks, private images, keys, and tokens. Administrator roles grant management access separately from resource quotas. Shared images have configured read access; private image copies remain owned and count toward the owner's quota.

Upload public keys once in the panel. They apply to the account's environments across nodes, and updates reach running environments. Administrators connect using their own enabled key and the target environment's user identity. Key changes affect new SSH connections after propagation.

Environment login routes open for running tasks and close when tasks end. Node transport uses a Unix socket locally or SSH with verified host keys and node-specific credentials. Access to the node socket grants machine-execution authority.

| Entry point | Default |
| --- | --- |
| Panel and API | TCP 8000, with an HTTPS reverse proxy for public deployment |
| Environment SSH | TCP 30022–30121, configured by `cluster.ssh_ports` |
| Node commands | Host Unix socket |
| Image registry | Node-local TCP 5000 with TLS and scoped credentials |
| K3s API | Host loopback TCP 6443 |

Configure HTTPS origin and cookie settings through the [deployment guide](operations.md). Keep `.env`, `DJANGO_SECRET_KEY`, node credentials, and module secrets in their protected state directories.

## Environments and builds

The runtime enforces CPU and memory limits, provides quota-aware resource views, and supplies AppArmor and seccomp profiles. Rootless Docker runs within the task's resources and stores its data on the work volume. Public datasets mount read-only, and workload service-account tokens are disabled.

Accelerator modules enforce their allocation model: NVIDIA MPS shares compute and memory; Ascend assigns whole chips exclusively. Allocation records persist for recovery. The node service is privileged and uses host network and process namespaces to manage this infrastructure.

Image repositories enforce ownership and validate imported archives, digests, sizes, and architecture. Builds use Rootless BuildKit with CPU and memory limits, a queue, cancellation, and timeouts. Private base-image access uses temporary repository-scoped credentials and verified TLS, including internal mirrors. Job credentials are removed when work ends.

## Files and recovery

Directory browsing and synchronization stay within configured roots and reject traversal and unsafe links. Dataset synchronization verifies checksums before committing selected subtrees; target copies follow the source, including removals. Home and work volumes stay on their own node.

Audit events store codes and parameters and display in the viewer's language. Notifications follow the recipient's language. Back up the database, encryption secret, node keys, registry metadata, data volumes, and allocation state together. Module uninstall tracks owned changes and retains user data and pre-existing resources.
