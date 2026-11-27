# API and Agent skill

[Home](../../README.md) · [简体中文](../zh/api.md)

The HTTP API at `/api/v1` enforces the same accounts, quotas, permissions, and approval rules as the web panel. Open `/api/v1/docs` or fetch `/api/v1/openapi.json` on your deployment for the live schema and enabled module fields.

## Authentication

- **Scripts and AI agents**: create a Bearer token under **Account & access → API & Agent skill** and send `Authorization: Bearer <token>`.
- **Web panel**: uses HttpOnly Django session cookies (`GET /api/v1/auth/csrf`, `POST /api/v1/auth/session`, `DELETE /api/v1/auth/session`) with `X-CSRFToken` on write requests.

Check your account and permissions with a Bearer token:

```bash
curl -H "Authorization: Bearer $GPU_PLATFORM_TOKEN" \
  "$GPU_PLATFORM_URL/api/v1/me"
```

Read `/me.permissions`, `/me.effective_limits`, and `/nodes` to discover available servers. `/resources`, `/gpus`, and `/images?available=true` accept `?node=ID`.

## Submit a task

Example batch job request (`POST /api/v1/tasks`):

```json
{
  "purpose": "Train baseline model",
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

- **Dev environments** use `mode: "interactive"` with timezone-aware `starts_at` and `ends_at`.
- **Batch jobs** use `mode: "batch"`, a `command`, and either `image` or a running `source_environment`.
- **GPU allocations** specify `compute_percent` and `memory_gib` per device in `gpu_requests` (either field accepts `"max"`), or pass slot-native quantities under `extensions.resources` and `extensions.allocations` (for example `npu_count` on Ascend servers). Avoid sending both `gpu_requests` and `extensions.allocations.gpu` in the same request.

## Endpoints overview

All paths are under `/api/v1`:

| Area | Paths |
| --- | --- |
| Account, tokens, and SSH keys | `/me`, `/me/language`, `/me/password`, `/me/notifications`, `/auth/tokens`, `/keys` |
| Servers, capacity, and schedule | `/nodes`, `/resources`, `/gpus`, `/timeline`, `/request-options` |
| Tasks, connections, and logs | `/tasks`, `/tasks/{id}`, `/tasks/{id}/connection`, `/tasks/{id}/logs` |
| Lifecycle actions | `/tasks/{id}/stop`, `/tasks/{id}/save`, `/tasks/{id}/extensions`, `/tasks/{id}/shorten` |
| Images, builds, and sync | `/images`, `/images/{id}`, `/builds`, `/snapshots`, `/images/{id}/sync`, `/syncs` |
| Storage and shared datasets | `/volumes`, `/datasets`, `/datasets/download` |
| Notifications inbox | `/inbox`, `/inbox/{id}`, `/inbox/{id}/read`, `/inbox/reminders` |
| Administration | `/admin/approvals`, `/tasks/{id}/review`, `/extensions/{id}/review`, `/admin/containers`, `/admin/performance`, `/admin/performance/history`, `/admin/usage/live`, `/admin/usage/history`, `/admin/storage`, `/admin/images`, `/admin/members`, `/admin/groups`, `/admin/notices`, `/admin/notifications`, `/admin/settings`, `/admin/audits` |

`GET /images` lists accessible permanent images. Use `available=true` to limit the results to verified images that are ready for tasks.

Owners and staff can request deletion of an idle private permanent image with a bodyless `DELETE /images/{id}`. The response wraps the same image record as `GET /images/{id}` in `data`. It returns `202 Accepted` with `data.state = "deleting"` while cleanup is pending, or `200 OK` with `data.state = "deleted"` if cleanup is already complete.

## Response format and idempotency

- Success responses wrap payloads in `{"data": ...}`. Paginated lists include `items`, `count`, `page`, `page_size`, and `next_page`.
- Error responses wrap failures in `{"error": {"code": "...", "message": "...", "fields": {...}}}`. Branch automation on `error.code`.
- Send `Accept-Language: zh-hans` to receive Simplified Chinese messages.
- Endpoints declaring `Idempotency-Key` support return the recorded result when retried with the same key and request body after a network timeout.

## Agent skill

Download `/api/v1/skill.zip` or use [skills/gpu-platform](../../skills/gpu-platform). Its Python standard-library client handles Bearer auth, idempotency keys, and waiting for task readiness:

```bash
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/me
python3 skills/gpu-platform/scripts/client.py request POST /api/v1/tasks --json @request.json
python3 skills/gpu-platform/scripts/client.py wait TASK_UUID --timeout 300
```

See [skills/gpu-platform/references/api.md](../../skills/gpu-platform/references/api.md) for the complete operation reference.
