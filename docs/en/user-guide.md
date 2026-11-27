# User guide

[Home](../../README.md) · [简体中文](../zh/user-guide.md)

This guide covers daily work in the web panel: setting up SSH access, creating dev environments and batch jobs, managing storage and images, and diagnosing common issues.

## 1. First-time setup

1. Open your team's platform URL and sign in with the account provided by your administrator.
2. Open **Account & access → Password** and update your initial password.
3. Open **Account & access → SSH keys** and add your SSH public key (for example `~/.ssh/id_ed25519.pub`).

Enabled keys apply to all of your dev environments on every server. Adding or disabling a key also updates running dev environments automatically.

## 2. Dev environments and batch jobs

| Task type | When to use it | How it ends |
| --- | --- | --- |
| **Dev environment** | Interactive coding, debugging, notebooks, or VS Code Remote-SSH | Stops automatically at the scheduled end time (or earlier if you stop or shorten it) |
| **Batch job** | Long training runs, preprocessing, or evaluations | Runs until the command exits and records logs to `/workspace/.platform/` |

### Request a dev environment

1. Go to **Overview** or **Dev environments** and click **New dev environment**.
2. Choose a **Server**, give the task a clear name, and pick an **Image**.
3. Select the number of GPUs/NPUs (or `0` for CPU-only), CPU cores, memory, and storage sizes. Click **Max** on a GPU field to allocate the maximum amount allowed by your remaining quota and the server's hardware.
4. Set the start and end time and submit.

Once the task status reaches **Running**, open the task detail page and copy the SSH command or the `VS Code / ~/.ssh/config` block.

### Extend or shorten an environment

On a running or scheduled dev environment's detail page:

- **Request extension** extends the end time within your per-environment extension allowance. If your account uses manual approval, the original end time stays active until an administrator approves the extension.
- **End earlier** updates the end time immediately so GPUs and memory return to your team sooner.

### Submit a batch job

1. Go to **Batch jobs** and click **Submit batch job**.
2. Choose either:
   - **Start from an image** to run a command in a clean saved image.
   - **Run from a running dev environment** to reuse packages you installed interactively. After approval, the platform saves an internal temporary image, stops the dev environment to release its resources, and starts the batch job. The platform automatically deletes the temporary image after its retention period.
3. Enter your shell command and submit.

## 3. Storage and files

Every task mounts three locations:

| Path inside container | Volume type | Persistence and sharing |
| --- | --- | --- |
| `/home/<username>` | **Home volume** | Shared across all your tasks on the same server. Persists after tasks end. Separate per server. |
| `/workspace` | **Work volume** | Dedicated to one task (or inherited when converting a dev environment into a batch job). Kept until the retention date shown on the task page. |
| `/datasets` | **Shared datasets** | Read-only team data on that server. Browse and download files from the **Shared datasets** page. |

Rootless Docker is available inside standard dev environments. Its images and containers are stored in `/workspace/.docker` and follow the work volume's retention period. On NVIDIA whole-GPU allocations, inner containers can run `docker run --rm --gpus all IMAGE COMMAND` within the task's allocated GPUs.

## 4. Images and data sync

Open **Images** to manage software environments:

- **Saved images**: shared team images and your permanent private images. Private images count toward your image storage quota.
- **Save a dev environment**: use the save action on a running dev environment to create a permanent private image.
- **Build from Dockerfile**: build a private image on a server that supports image builds, optionally pulling a build context from an HTTPS Git repository.
- **Build & save history**: inspect progress and full build logs.

Open **Data sync** (when enabled across multiple servers) to copy an accessible image to another server with the same CPU architecture.

## 5. Connecting between dev environments

When **Workspace SSH** is enabled on your servers, a running dev environment's **Connect** panel includes an internal SSH command and a list of your other running dev environments. Run that command (or `scp` / `sftp`) directly inside another running dev environment under your account without configuring extra SSH keys.

## 6. Automation and preferences

- **API & Agent skill** (**Account & access → API & Agent skill**): create Bearer tokens, open `/api/v1/docs`, or download `gpu-platform-skill.zip` for AI coding agents. See [API and Agent skill](api.md).
- **Preferences** (**Account & access → Preferences**): switch between Light, Dark, and System themes, change language (English / Simplified Chinese), inspect your effective quotas, and configure personal notification channels if enabled.

## 7. Common questions

- **My task stays in Submitted**: your account uses manual approval. Wait for an administrator to review it in **Approvals**.
- **My task stays in Queued**: the server's GPUs, CPU, or memory are currently occupied by other running tasks. It starts automatically once enough resources free up.
- **SSH connection refused or denied**: verify the task status is **Running**, check that your SSH public key is enabled under **Account & access → SSH keys**, and copy the exact port from the task's **Connect** card.
- **Work volume full or approaching retention expiry**: move outputs you want to keep to `/home/<username>` or download them locally before the retention deadline shown on the task page.
