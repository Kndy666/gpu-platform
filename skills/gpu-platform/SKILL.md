---
name: gpu-platform
description: Operate an existing compute resource platform through its HTTP API to request dev environments, submit batch jobs, manage images and SSH keys, and run authorized administration tasks.
---

Use the deployment's HTTP API (`/api/v1`) to complete the user's request. Resolve the deployment URL from `GPU_PLATFORM_URL` or `--url`. Check whether `GPU_PLATFORM_TOKEN` is set without displaying its value. Users can issue tokens from **Account & access → API & Agent skill**. Ask for missing URL or token credentials when required.

Run the bundled Python standard-library client (paths relative to this skill directory):

```bash
python3 scripts/client.py request GET /api/v1/me
python3 scripts/client.py request GET /api/v1/nodes
python3 scripts/client.py request GET /api/v1/resources
python3 scripts/client.py request GET /api/v1/images --query available=true
```

Inspect `/api/v1/openapi.json` for deployed fields, methods, extensions, and permissions. Read [references/api.md](references/api.md) for task, image, storage, and administration workflows. API keys and error codes are stable English identifiers; send `Accept-Language: zh-hans` when Simplified Chinese messages help the user.

Use `/me.permissions`, `effective_limits`, and `/resources.request_capacity` to verify available actions. Account roles govern administration access separately from resource quotas. New members inherit group defaults and then keep independent limits and approval settings.

Select an enabled server from `/nodes` and query its capacity and images with `?node=ID`. Task and build requests take `node`. Home and work volumes belong to that server. Starting a batch job from `source_environment` uses the source environment's server. User quotas apply across all servers. Image sync creates an independent copy on another server with the same CPU architecture and counts toward the owner's image quota.

For a **dev environment**, verify enabled SSH keys and ready images, then submit `mode=interactive` with CPU, memory, work volume size, and timezone-aware `starts_at`/`ends_at` (interpret natural-language times in `/me.timezone`). Obtain or generate a missing SSH public key with user authorization. Omit accelerators or use `gpu=0` for CPU-only environments.

For **GPU requests**, each `gpu_requests` entry specifies `compute_percent` and `memory_gib` (or `"max"`, resolved against remaining quota and hardware capacity). Use `/gpus.request_limits` for per-device and total bounds. Array order sets CUDA device order. For slot-native resources (such as Ascend `npu_count`), pass quantities in `extensions.resources` and `extensions.allocations` without duplicating `gpu_requests`.

For a **batch job**, submit `mode=batch`, `command`, and either `image` or a running `source_environment`. After approval, the platform saves the source environment as a temporary image, stops it to free resources, and queues the batch job. Matching work volume size reuses the volume; a different size triggers an offline copy. Batch jobs run until their command exits.

Use a stable `Idempotency-Key` for task submission, image builds, and environment saves. The bundled client generates one automatically for POST requests and returns it on network errors so you can safely retry with the same key and payload.

Report the task ID and approval state after submission. Run `python3 scripts/client.py wait TASK_ID --timeout 300` to wait for SSH readiness. For future schedules, pending approvals, or timeouts, report the current status and keep the task. Once running, return `connection.command` and `connection.config`. Inspect progress via `/tasks/{id}` and `/tasks/{id}/logs`; persistent logs live in `/workspace/.platform/`.

- Extend a dev environment with `POST /tasks/{id}/extensions` (`ends_at` and `reason`). Pending manual approvals keep the existing deadline until reviewed. Inspect `extension_history` on task detail for past requests.
- Shorten a dev environment immediately with `POST /tasks/{id}/shorten` (`ends_at`).
- Administrators can adjust end times with `POST /tasks/{id}/review` (`action=adjust_time`, `ends_at`, optional `reason`).

Use administrator endpoints strictly within user-authorized scope. Administrators can inspect `/admin/containers` and connect using their own enabled SSH key (`administrator_access.ready`) as the environment's user. Check `fresh` and `sampled_at` before interpreting `/admin/performance` readings; missing hardware sensors are `null`. Report structured API errors and fix invalid fields from `error.fields`.
