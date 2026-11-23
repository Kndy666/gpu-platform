# API operations

Configure `GPU_PLATFORM_URL` and `GPU_PLATFORM_TOKEN`. The client sends JSON and `Authorization: Bearer …`. Success responses contain `data`; errors contain `error.code`, `error.message`, and optional field details. Paginated data contains `items`, `count`, `page`, `page_size`, and `next_page`. The deployment's `/api/v1/openapi.json` describes available operations and extension fields.

All paths below are relative to `/api/v1` and have no trailing slash.

| Operation | Method and path |
| --- | --- |
| Issue or revoke the current token | `POST /auth/token`, `DELETE /auth/token` |
| Manage personal tokens | `GET/POST /auth/tokens`, `GET/DELETE /auth/tokens/{id}` |
| Read account and effective limits | `GET /me` |
| Set language or notification receivers | `PATCH /me/language`, `GET/PATCH /me/notifications` |
| Change password | `POST /me/password` |
| Inspect nodes and capacity | `GET /nodes`, `GET /resources`, `GET /gpus` |
| Inspect reservations | `GET /timeline?date=YYYY-MM-DD` |
| Manage SSH keys | `GET/POST /keys`, `PATCH/DELETE /keys/{id}` |
| Request and inspect tasks | `GET/POST /tasks`, `GET /tasks/{id}` |
| Connect or read logs | `GET /tasks/{id}/connection`, `GET /tasks/{id}/logs?tail=200` |
| Stop a task | `POST /tasks/{id}/stop` with `{}` |
| Save an environment | `POST /tasks/{id}/save` with `{}` |
| Request an extension | `GET/POST /tasks/{id}/extensions` |
| Inspect save operations | `GET /snapshots`, `GET /snapshots/{id}` |
| Find accessible images | `GET /images?available=true`, `GET /images/{id}` |
| Synchronize an image and inspect progress | `POST /images/{id}/sync` with `{"target": NODE_ID}`, `GET /syncs` |
| Retain or delete an image | `POST /images/{id}/keep`, `DELETE /images/{id}` |
| Build an image | `GET/POST /builds`, `GET /builds/{id}` |
| Read personal and work volumes | `GET /volumes`, `GET /volumes/{id}` |
| Browse or download public data | `GET /datasets`, `GET /datasets/download?path=...` |

Task and build requests accept a numeric `node` ID. Filter resources, GPU inventory, images, tasks, builds, snapshots, volumes, timeline entries, and public directories with `?node=ID`. Use IDs returned by `/nodes`. An image must be available on the selected node; synchronize an accessible image to another node of the same CPU architecture before using its copy. Personal and work volumes remain local to their nodes.

For development environments, submit a JSON file containing `mode=interactive`, `purpose`, `cpu`, `memory_gib`, `workspace_gib`, `starts_at`, and `ends_at`; use timezone-aware ISO 8601 timestamps derived from the user's requested period. Include an image ID or use the deployment's default shared image.

```bash
python3 scripts/client.py request POST /api/v1/tasks --json @request.json
python3 scripts/client.py wait TASK_ID --timeout 300
```

For batch execution, use `mode=batch`, `command`, and `image` or `source_environment`. Optional `working_dir` and `environment` control the process context. Environment values are single-line strings; identity and runtime variables are reserved. Batch tasks finish when their command exits.

A source environment is saved and stopped after approval. Its personal volume is reused. If `workspace_gib` is omitted or matches, the work volume is reused. A changed capacity creates a destination volume and copies data after the source has stopped; the old volume is retired after a successful copy. The `preparing` state includes this work.

A GPU request has entries such as `[{"compute_percent":100,"memory_gib":8},{"compute_percent":50,"memory_gib":4}]`. `compute_percent` ranges from 1 to 100; the selected GPU module validates supported increments. Memory is expressed in GiB. Either field accepts `"max"`; the response records resolved values. The optional `gpu` count must equal the array length. `gpu_allocations` associates each request slot with its physical device and allocation. An unlimited quota uses `null` for the corresponding total limit; physical capacity still controls admission.

The `/resources.request_capacity` object supplies request limits. `/timeline.groups` groups `entries`, `blocks`, and `queued` by node. `queued` covers waiting tasks whose start time has arrived for the current day; future reservations appear in `entries`. Visibility follows the caller's permissions.

Creation operations declare `Idempotency-Key` support in OpenAPI. Reusing the same key, user, method, path, and parameters returns the recorded result. Change the key when changing the request.

Task states include `submitted`, `approved`, `scheduled`, `preparing`, `queued`, `starting`, and `running`. Terminal states include `succeeded`, `failed`, `canceled`, `expired`, `cleaned`, and `rejected`. Connection data becomes available when the environment is running and its login path is ready.

# Administration

Use `/me.permissions` to determine administration rights. Member limits and role permissions are independent. Administrators may manage tasks, images, and idle volumes across users; personal key, token, build, and save records follow their ownership rules.

| Operation | Method and path |
| --- | --- |
| Overview and approvals | `GET /admin/overview`, `GET /admin/approvals?kind=extensions` |
| Review a task | `POST /tasks/{id}/review` |
| Review an extension | `POST /extensions/{id}/review` |
| Inspect all environments | `GET /admin/containers` |
| Inspect hardware performance | `GET /admin/performance?node=ID` |
| Configure notifications | `GET/PATCH /admin/notifications` |
| Manage members | `GET/POST /admin/members`, `GET/PATCH /admin/members/{id}` |
| Manage groups | `GET/POST /admin/groups`, `GET/PATCH/DELETE /admin/groups/{id}` |
| Manage execution nodes | `POST /nodes`, `PATCH /nodes/{id}` |
| Inspect storage and images | `GET /admin/storage`, `GET /admin/images` |
| Delete an idle volume | `DELETE /volumes/{id}` |
| Read audit events | `GET /admin/audits` |
| Configure retention | `GET/PATCH /admin/settings` |
| Synchronize public directories | `POST /admin/datasets/sync` with `source`, `target`, and `paths` |

Task review uses `action=approve`, `reject`, `stop`, or `extend`; a rejection needs `reason`, and an extension needs `ends_at` and `reason`. Priorities are 0 or 100. Extension review uses `approve` or `reject`. Successful user extensions consume the member's allowance, while pending or rejected requests retain the existing deadline and allowance.

Member and group settings share resource fields, `approval_mode`, `interactive_hours`, and `extension_limit`. New members inherit group defaults and subsequently store independent settings. An `extension_limit` of 0 disables user-requested extensions. `unlimited=true` clears quota values. Set `make_default=true` to select the default group; deleting a populated group requires a destination group ID.

Performance responses include `fresh`, `sampled_at`, and CPU, memory, GPU, disk, and network measurements. Inspect sample freshness and nullable hardware readings. Container searches accept `q`, `status`, `mode`, and `node`. Administrator connection responses include `administrator_access` and the target environment's username.

Token creation accepts `expires_in` in seconds; 0 means no expiration. Password changes, account disabling, and token revocation invalidate access. SSH public-key changes propagate to running environments and affect new connections after the mount update.

# Environment Docker

Standard development environments provide Rootless Docker. GPU programs can use `docker run --rm --gpus all IMAGE COMMAND`; the inner container shares the outer task's assigned GPU resources. Image layers, containers, and Docker volumes are stored in `/workspace/.docker` and follow work volume retention. The environment receives read-only registry credentials. Publish platform images through build or save operations.
