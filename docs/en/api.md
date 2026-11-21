# Automate your work

[Home](../../README.md) · [简体中文](../zh/api.md)

Use the same accounts, quotas, and approval rules from scripts or an agent. The API lives at `/api/v1`; your deployment's `/api/v1/docs` and `/api/v1/openapi.json` describe its operations and enabled module fields.

## Connect

Create a token on the panel's API access page. Set `GPU_PLATFORM_URL` and `GPU_PLATFORM_TOKEN`, then check your account:

```bash
curl -H "Authorization: Bearer $GPU_PLATFORM_TOKEN" \
  "$GPU_PLATFORM_URL/api/v1/me"
```

Read `/me.permissions` for available actions and `/nodes` for execution hosts. `/resources`, `/gpus`, and `/images?available=true` accept `?node=ID` to help choose a task's resources and image.

## Submit a task

For example, send this batch request to `POST /api/v1/tasks`, replacing the node and image IDs with values from your deployment:

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

Interactive environments use `mode: interactive` and timezone-aware `starts_at` and `ends_at`. Batch jobs use an image or `source_environment` and finish when their command exits. Read the returned task state, then query its connection or logs as it progresses through approval, preparation, and execution.

| Need | Paths below `/api/v1` |
| --- | --- |
| Manage account, tokens, or public keys | `/me`, `/auth/tokens`, `/keys` |
| Inspect capacity and reservations | `/nodes`, `/resources`, `/gpus`, `/timeline` |
| Request, connect, or read logs | `/tasks`, `/tasks/{id}/connection`, `/tasks/{id}/logs` |
| Stop, save, or extend an environment | `/tasks/{id}/stop`, `/tasks/{id}/save`, `/tasks/{id}/extensions` |
| Build, find, or copy images | `/builds`, `/images`, `/images/{id}/sync`, `/syncs` |
| Work with storage and public data | `/volumes`, `/datasets`, `/datasets/download` |
| Review requests and manage the team | `/admin/approvals`, `/admin/members`, `/admin/groups`, `/admin/containers` |

GPU request entries contain `compute_percent` and `memory_gib` per device. Custom module quantities use `extensions.resources`, and repeated allocations use `extensions.allocations`. Discover resource keys, units, and limits from the node's resource catalog. Ascend requests use `npu_count` in the resource extensions. Image synchronization needs the same CPU architecture; members copy their own private images, and administrators can copy shared images. Private copies count toward their owner's quota.

Successful JSON responses contain `data`. Errors contain `error.code`, `error.message`, and optional field details. Use codes for automation. `Accept-Language: zh-hans` selects Chinese messages. Paginated lists include `items`, `count`, `page`, `page_size`, and `next_page`.

For operations that declare `Idempotency-Key` support, retry a failed connection with the same key and body. OpenAPI documents methods, pagination, filters, and required fields.

## Use the agent skill

Download `/api/v1/skill.zip` or use [skills/gpu-platform](../../skills/gpu-platform). Its English instructions and Python standard-library client cover resource requests, environment connections, and authorized administration:

```bash
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/me
python3 skills/gpu-platform/scripts/client.py request POST /api/v1/tasks --json @request.json
python3 skills/gpu-platform/scripts/client.py wait TASK_UUID --timeout 300
```

The client keeps the idempotency key when a request fails. The [operation reference](../../skills/gpu-platform/references/api.md) covers task, image, storage, and administration workflows.
