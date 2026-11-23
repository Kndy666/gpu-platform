---
name: gpu-platform
description: Operate an existing compute resource platform through its API to request development environments, run programs, manage images, and inspect resources or authorized administration tasks.
---

Use the deployment's HTTP API for the resource operation the user requested. Resolve the deployment URL from `GPU_PLATFORM_URL` or the client's `--url` argument. Read whether `GPU_PLATFORM_TOKEN` is configured without displaying its value. Users can issue tokens from their API access page. Ask for missing deployment information or credentials when an API call needs them.

Use the bundled Python standard-library client, with paths relative to this skill directory:

```bash
python3 scripts/client.py request GET /api/v1/me
python3 scripts/client.py request GET /api/v1/nodes
python3 scripts/client.py request GET /api/v1/resources
python3 scripts/client.py request GET /api/v1/images --query available=true
```

Read `/api/v1/openapi.json` for the deployed fields, methods, extensions, and permissions. Read [references/api.md](references/api.md) for task, storage, image, and administration workflows. API field names and event codes are stable English identifiers; use `Accept-Language` when localized messages help the user.

Use `/me.permissions`, `effective_limits`, and resource `request_capacity` to determine available actions. Account roles grant administration access. New members inherit group defaults and then retain their own limits and approval settings. Permissions are evaluated for every request.

Choose an enabled execution node from `/nodes`; query its resources and images with `?node=ID`. Task and build bodies use `node`. Personal and work volumes belong to that node. Starting a program from `source_environment` uses the source node. User quotas include usage across nodes. Image synchronization creates an independently retained copy and consumes image quota; discover the sync operation in OpenAPI and verify that source and target architectures match.

For a development environment, check the user's enabled SSH keys and available images, then submit `mode=interactive` with CPU, memory, work volume capacity, and timezone-aware `starts_at`/`ends_at`. Interpret natural-language dates using the API's `timezone`. Obtain a missing public key from the user or generate a key within their authorization. An environment without GPUs uses `gpu=0`.

For GPU requests, each `gpu_requests` entry specifies `compute_percent` and `memory_gib`. Use `/gpus.request_limits` for per-device and total limits. A field may use `"max"`; the service resolves it against remaining quota and physical capacity. Array order determines CUDA device order. GPU allocations must fit compute and memory on each physical device. Resource availability during execution is managed by the node's queue.

For a program, submit `mode=batch`, a command, and either an image or the user's running `source_environment`. After approval, the platform saves a source environment, stops it, and queues the program. Matching work volume capacity reuses the volume; a changed capacity triggers an offline copy. A batch program runs until its command exits. If the user has not supplied a command, complete the requested development environment setup first.

Use a stable `Idempotency-Key` for task creation, image builds, and environment saves. The client generates a key for POST requests and includes it in a connection-failure result. Retry the same operation with that key and the same parameters. Query the resulting resource before taking further action.

Report the task ID and observed approval state after submission. `python3 scripts/client.py wait TASK_ID --timeout 300` waits for a connection. For a future reservation, pending approval, or timeout, report the current state and retain the task. Once ready, return `connection.command` and `connection.config`. Query task status and logs for progress; persistent logs live under `/workspace/.platform/`.

An extension uses `POST /tasks/{id}/extensions` with timezone-aware `ends_at` and a reason. Inspect the extension record for its approval result. The original deadline remains effective while manual approval is pending. Successful extensions consume the member's extension allowance.

Use administrator endpoints for the scope the user authorized. Administrators can query `/admin/containers` and obtain a target's connection data; they connect as the target environment's user with their own enabled SSH key. Check `administrator_access.ready`. Key and role changes propagate to running environments and affect subsequent SSH connections.

Inspect `fresh` and `sampled_at` before interpreting performance data. A missing hardware reading is represented by `null`. For stopping tasks, deleting images or volumes, or changing members and groups, use the exact requested objects and scope. Report structured API errors and adjust invalid parameters from their field details.
