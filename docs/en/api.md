# API and automation

The public API is served at `/api/v1`. Django Ninja generates `/api/v1/openapi.json` from the operation schemas; `/api/v1/docs` provides an interactive reference. The panel and HTTP handlers call the same public control services.

## Authentication and requests

Create a named token from the panel's API access page, or use `POST /api/v1/auth/token` with credentials accepted by the configured authentication module. Send it as `Authorization: Bearer TOKEN`. Account ownership and administrator permissions apply to each operation.

```bash
curl -H "Authorization: Bearer $GPU_PLATFORM_TOKEN" \
  "$GPU_PLATFORM_URL/api/v1/me"
```

Successful JSON operations use a `data` envelope. Errors carry `error.code`, a localized `error.message`, and optional field details. Use stable codes for automation. Send `Accept-Language: zh-hans` for Chinese messages; the API defaults to English. Field names, resource keys, event codes, and identifiers stay language-independent.

Mutating operations that declare idempotency accept `Idempotency-Key`. Reuse the same key and body after a transport failure. OpenAPI marks the operations and accepted request fields.

## Operations

| Area | Paths below `/api/v1` |
| --- | --- |
| Account and tokens | `/me`, `/me/password`, `/me/language`, `/auth/token`, `/auth/tokens` |
| Capabilities and capacity | `/nodes`, `/resources`, `/gpus`, `/timeline` |
| SSH keys | `/keys`, `/keys/{id}` |
| Requests and execution | `/tasks`, `/tasks/{id}`, `/tasks/{id}/connection`, `/tasks/{id}/logs`, `/tasks/{id}/stop` |
| Approval and extensions | `/tasks/{id}/review`, `/tasks/{id}/extensions`, `/extensions/{id}/review` |
| Image preparation | `/tasks/{id}/save`, `/snapshots`, `/builds`, `/images` |
| Storage and public data | `/volumes`, `/datasets`, `/datasets/download` |
| Synchronization | `/syncs`, `/images/{id}/sync`, `/admin/datasets/sync` |
| Administration | `/admin/containers`, `/admin/approvals`, `/admin/members`, `/admin/groups`, `/admin/storage`, `/admin/images`, `/admin/audits`, `/admin/settings`, `/admin/performance`, `/admin/notifications` |

List operations support the filters and pagination documented in OpenAPI. Node-bound records include their node identity. Resource responses expose capability-driven extensions for installed modules. Discover node capabilities before submitting resource requests.

A batch request chooses a node and an accessible image or source environment:

```json
{
  "purpose": "Process a dataset",
  "mode": "batch",
  "node": 1,
  "image": "IMAGE_UUID",
  "cpu": 4,
  "memory_gib": 8,
  "workspace_gib": 40,
  "gpu_requests": [],
  "command": "python /workspace/main.py"
}
```

Interactive requests use `mode: interactive`, `starts_at`, and `ends_at`. The API reports approval and preparation states alongside the execution lifecycle. A successful submission reserves or queues work; read the task's state and connection details to observe dispatch.

GPU requests specify a `compute_percent` and `memory_gib` for each requested device. The default module interprets compute percentage as a share of one GPU's SM capacity. Request arrays determine logical device order; node allocation determines physical devices. Standard CPU and memory fields are API projections of the registered resource keys. Custom quantities use `extensions.resources`; grouped allocations use `extensions.allocations`, mapping a group name to a list of resource-keyed amounts. `extensions.volume_sizes` supplies work volume capacity per container; personal volume capacity comes from the member setting on each node. For example: `"extensions": {"resources": {"licenses": 2}, "allocations": {"accelerators": [{"accelerator_memory": 8}]}}`. Discover keys, units, and allocation metadata from the resource catalog before submitting.

## Synchronization

`POST /images/{id}/sync` accepts a target node and optional bandwidth limit. Members can synchronize only their own private images. Administrators can synchronize shared images; destination copies automatically remain shared. The control service checks image access, matching architecture, enabled capabilities, and the owner's total image quota before queuing the copy. `GET /syncs` returns jobs visible to the caller, including transferred bytes, state, and errors represented as codes and parameters.

Administrators use `POST /admin/datasets/sync` with source, target, and selected relative paths. The configured public-directory root bounds each operation. A completed image synchronization creates a separate artifact with its own retention lifecycle.

## Agent skill

The English-only skill lives in [skills/gpu-platform](../../skills/gpu-platform). It can also be downloaded from `/api/v1/skill.zip`. Configure `GPU_PLATFORM_URL` and `GPU_PLATFORM_TOKEN`, then use its standard-library client:

```bash
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/me
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/openapi.json
python3 skills/gpu-platform/scripts/client.py request POST /api/v1/tasks --json @request.json
python3 skills/gpu-platform/scripts/client.py wait TASK_UUID --timeout 300
```

The client records the idempotency key used for a failed request so a retry can reuse it. Its [operation reference](../../skills/gpu-platform/references/api.md) covers command examples and task polling.
