# API operations reference

Configure `GPU_PLATFORM_URL` and `GPU_PLATFORM_TOKEN`. The client sends JSON with `Authorization: Bearer …`. Success payloads are wrapped in `{"data": ...}`; errors return `{"error": {"code", "message", "fields"}}`. Paginated endpoints return `items`, `count`, `page`, `page_size`, and `next_page`. Fetch `/api/v1/openapi.json` for exact deployed schemas.

All paths below are relative to `/api/v1` without a trailing slash.

## User operations

| Operation | Method and path |
| --- | --- |
| Issue or revoke current Bearer token | `POST /auth/token`, `DELETE /auth/token` |
| Manage personal API tokens | `GET/POST /auth/tokens`, `GET/DELETE /auth/tokens/{id}` |
| Account profile, limits, and features | `GET /me` |
| Language, notifications, and password | `PATCH /me/language`, `GET/PATCH /me/notifications`, `POST /me/password` |
| Servers, capacity, and schedule | `GET /nodes`, `GET /resources`, `GET /gpus`, `GET /timeline?date=YYYY-MM-DD`, `GET /request-options` |
| SSH keys | `GET/POST /keys`, `PATCH/DELETE /keys/{id}` |
| Tasks | `GET/POST /tasks`, `GET /tasks/{id}` |
| SSH connection and logs | `GET /tasks/{id}/connection`, `GET /tasks/{id}/logs?tail=200` |
| Stop or save a task | `POST /tasks/{id}/stop` (`{}`), `POST /tasks/{id}/save` (`{}`) |
| Extend or shorten a dev environment | `GET/POST /tasks/{id}/extensions`, `POST /tasks/{id}/shorten` |
| Environment save history | `GET /snapshots`, `GET /snapshots/{id}` |
| Permanent images and deletion | `GET /images?available=true`, `GET /images/{id}`, `DELETE /images/{id}` |
| Dockerfile builds | `GET /builds/options`, `GET/POST /builds`, `GET /builds/{id}` |
| Image and data sync | `GET /syncs/options`, `POST /images/{id}/sync`, `GET /syncs` |
| Volumes and shared datasets | `GET /volumes`, `GET /volumes/{id}`, `GET /datasets`, `GET /datasets/download?path=...` |
| Notifications inbox | `GET /inbox`, `GET /inbox/{id}`, `POST /inbox/{id}/read`, `POST /inbox/reminders` |

Owners and staff can delete idle private permanent images with `DELETE /images/{id}` without a request body. The response returns the image record in `data` with `202 Accepted` and `state="deleting"` while cleanup is pending, or `200 OK` and `state="deleted"` once cleanup is complete.

### Submitting tasks

- Pass numeric `node` IDs from `/nodes`. Filter `/resources`, `/gpus`, `/images`, `/tasks`, `/builds`, `/snapshots`, `/volumes`, `/timeline`, and `/datasets` with `?node=ID`.
- **Dev environments**: `mode="interactive"`, `purpose`, `cpu`, `memory_gib`, `workspace_gib`, timezone-aware ISO 8601 `starts_at` and `ends_at`, and `image` (or default shared image).
- **Batch jobs**: `mode="batch"`, `command`, and either `image` or `source_environment`, plus optional `working_dir` and `environment` (single-line strings; identity and runtime variables are reserved).
- **GPU requests**: `gpu_requests` entries contain `compute_percent` (1–100) and `memory_gib` (GiB); either field accepts `"max"`. The task detail response keeps slot quantities in `extensions.resources` and lists the 10 most recent time extensions in `extension_history`.
- **Idempotency**: creation endpoints support `Idempotency-Key`. Reuse the same key and body when retrying a failed connection; use a new key for modified parameters.

Task states: `submitted`, `approved`, `scheduled`, `preparing`, `queued`, `starting`, `running`. Terminal states: `succeeded`, `failed`, `canceled`, `expired`, `cleaned`, `rejected`.

## Administration operations

Check `/me.permissions` for staff capabilities. Administrators can inspect and manage tasks, images, and idle volumes across users.

| Operation | Method and path |
| --- | --- |
| Overview and approval queues | `GET /admin/overview`, `GET /admin/approvals?kind=tasks\|extensions` |
| Review task or adjust end time | `POST /tasks/{id}/review` (`action=approve\|reject\|stop\|extend\|adjust_time`) |
| Review extension request | `POST /extensions/{id}/review` (`action=approve\|reject`) |
| Search all workloads | `GET /admin/containers` (`q`, `status`, `mode`, `node`) |
| Live load and metrics history | `GET /admin/performance?node=ID`, `GET /admin/performance/history` |
| Live allocation and usage stats | `GET /admin/usage/live`, `GET /admin/usage/history` |
| Announcements and reminder rules | `GET /admin/notices/composer`, `POST /admin/notices`, `GET /admin/notices/history`, `POST /admin/notices/{id}/stop`, `GET/PATCH /admin/notifications` |
| Members | `GET /admin/members/options`, `GET/POST /admin/members`, `GET/PATCH /admin/members/{id}` |
| User groups | `GET /admin/groups/options`, `GET/POST /admin/groups`, `GET/PATCH/DELETE /admin/groups/{id}` |
| Servers | `GET /admin/nodes/options`, `POST /nodes`, `PATCH /nodes/{id}` |
| Storage pools, volumes, and images | `GET /admin/storage`, `DELETE /volumes/{id}`, `GET /admin/images` |
| Retention settings and audits | `GET/PATCH /admin/settings`, `GET /admin/audits` |
| Sync shared datasets | `POST /admin/datasets/sync` (`source`, `target`, optional `paths`, `bandwidth`) |

- `POST /tasks/{id}/review` takes `priority` (`0` or `100`) on `approve`, requires `reason` on `reject`, and requires `ends_at` on `extend` or `adjust_time`.
- Member and group payloads share resource quota fields, `home_quota_gib`, `approval_mode`, `interactive_hours`, `concurrent_tasks`, `extension_limit`, `unlimited`, and `extensions.{resources,per_allocation}`. Deleting a non-empty group requires `destination` group ID.
- Token creation accepts `expires_in` in seconds (`0` = never expires).

## Rootless Docker inside environments

Standard dev environments include Rootless Docker storing images and containers in `/workspace/.docker` (subject to work volume retention). Under whole-card NVIDIA GPU allocations, inner containers can run `docker run --rm --gpus all IMAGE COMMAND` within the task's assigned GPUs. Publish reusable images through build or save operations.
