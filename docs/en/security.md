# Security and data boundaries

[Home](../../README.md) · [简体中文](../zh/security.md)

Platform accounts anchor resource ownership, quotas, SSH keys, and API tokens. Authentication modules verify identity while control evaluates permissions on every API call.

## Authentication and access control

- **Browser sessions**: the Vue panel authenticates against `/api/v1/auth/session` using HttpOnly Django session cookies and CSRF tokens (`X-CSRFToken`) over a single same-origin Nginx entrypoint.
- **API tokens**: scripts and AI agents send `Authorization: Bearer <token>`. An invalid `Authorization` header is rejected with `401` even if a browser session cookie is present. Changing a password invalidates existing API tokens while keeping the user's current browser session signed in.
- **Roles and quotas**: members manage their own tasks, private images, SSH keys, and tokens. Administrator privileges are independent of compute quotas.
- **SSH keys**: upload public keys once under **Account & access → SSH keys**. Enabled keys propagate to running dev environments across all servers. Administrators connect to user environments using their own enabled SSH key and the target environment's login identity.

## Network boundaries

| Endpoint | Default exposure |
| --- | --- |
| Web panel and `/api/v1` | TCP `8000` on `panel` (Nginx); place an HTTPS reverse proxy in front for public access |
| Control API (`web`) | Internal Docker Compose network TCP `8000` |
| Dev environment SSH | TCP `30022–30121` (`cluster.ssh_ports`), open only while tasks run |
| Node commands | Host Unix socket locally, or reverse SSH with verified `known_hosts` and per-node keys |
| Image registry (Zot) | Node-local TCP `5000` with TLS and scoped credentials |
| K3s API | Host loopback TCP `6443` |

Keep `.env`, `DJANGO_SECRET_KEY`, node SSH keys, and notification tokens inside protected root-only directories.

## Workload and build isolation

- The K3s runtime enforces CPU, memory, and ephemeral storage limits, exposes quota-aware resource views via LXCFS, disables Kubernetes service-account tokens inside workloads, and applies AppArmor and seccomp profiles.
- Rootless Docker runs within the task's resource limits and stores its data in `/workspace/.docker`.
- Shared datasets mount read-only at `/datasets`.
- Accelerator modules enforce their isolation model (NVIDIA MPS partitions or exclusive Ascend chips) and persist allocation ledgers for restart recovery. The `node` container runs privileged with host network and PID namespaces to manage local infrastructure.
- Rootless BuildKit runs image builds with CPU/memory limits, queueing, cancellation, timeouts, and short-lived scoped registry credentials removed on completion.

## Files, audits, and backups

- Dataset browsing and synchronization stay within configured roots and reject path traversal and unsafe symlinks. Dataset sync verifies checksums before committing changes.
- Audit logs record structured action codes and parameters, rendered in the viewer's language.
- Back up the SQLite/SQL database, `.env`, node keys, `metrics-history/`, registry metadata, allocation ledgers, and data volumes together. Uninstall removes only module-owned host setup and preserves user data.
