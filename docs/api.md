# API 与 Agent

计算资源平台地址由 `config.yaml` 的 `platform.https_origin` 配置。登录网页后进入「API 访问」，选择个人令牌有效期（1 天、7 天、30 天、90 天、1 年或永久），并下载 Agent skill。默认有效期 7 天。

| 入口 | 地址 |
|---|---|
| 访问令牌与下载 | `/api/` |
| API 根地址 | `/api/v1/` |
| OpenAPI 3.0 | `/api/v1/openapi.json` |
| Agent skill ZIP | `/api/v1/skill.zip` |

API 使用 `Authorization: Bearer TOKEN`，请求正文为 JSON。账号权限、用户组额度、审核和资源回收规则与网页一致。修改密码、停用账号会使令牌失效；`DELETE /api/v1/auth/token` 撤销当前令牌。

`POST /api/v1/auth/token` 可传 `expires_in` 自定义整数秒数；`0` 表示永久。永久令牌返回 `expires_in: 0`、`expires_at: null`。

生成时可填写 `name`。网页令牌列表支持刷新后显示、复制和删除；删除立即使该令牌失效。`GET /api/v1/auth/tokens` 返回本人的令牌元数据，`DELETE /api/v1/auth/tokens/{id}` 删除指定令牌。列表接口不返回令牌原文。

已有令牌可通过 `POST /api/v1/auth/tokens` 为当前账号生成新令牌，无需再次提交密码。`GET /api/v1/auth/tokens/{id}` 返回本人的令牌原文，用于与网页相同的显示和复制操作。

网页与 API 的操作、数据和权限对照见 [完整对照表](api-parity.md)。账号角色与额度由服务端实时读取，`GET /api/v1/me` 的 `permissions` 提供管理操作权限；不限额用户组不自动授予管理员权限。

成功响应为 `{"data":...}`，错误响应为 `{"error":{"code":...,"message":...,"fields":...}}`。列表支持 `page` 和 `page_size`，每页最多 100 条。时间使用带时区的 ISO 8601，如 `2026-11-04T14:00:00+08:00`。

| 功能 | 接口 |
|---|---|
| 账号、配额、资源与时间轴 | `/me`、`/me/password`、`/resources`、`/timeline` |
| SSH 公钥 | `/keys`、`/keys/{id}` |
| 开发环境、运行程序 | `/tasks`、`/tasks/{id}` |
| 连接、日志、停止、保存、审批 | `/tasks/{id}/connection`、`/logs`、`/stop`、`/save`、`/review` |
| 环境保存、镜像、构建 | `/snapshots`、`/images`、`/builds` 及各自详情接口 |
| 卷与公用目录 | `/volumes`、`/datasets`、`/datasets/download` |
| 管理 | `/admin/overview`、`/containers`、`/members`、`/groups`、`/storage`、`/images`、`/audits`、`/settings` |

表中接口以 `/api/v1` 开头；连接等任务操作沿用 `/tasks/{id}`，管理操作沿用 `/admin`。方法、字段与支持 `Idempotency-Key` 的操作由 OpenAPI 列出。相同用户、方法、路径、key 和参数重试会返回原结果；改变参数应使用新 key。

`GET /admin/containers` 仅管理员可用，列出已有运行实例的全体任务，支持 `q`（名称、所属用户名或容器编号）、`status`、`mode` 与分页。对应网页“管理 → 容器”。使用 `GET /tasks/{id}` 查看详情，`GET /tasks/{id}/connection` 取得 SSH 命令与 config；管理员响应额外包含 `administrator_access`，其 `ready` 表示本人已配置启用中的公钥，`keys` 提供密钥 ID、名称和指纹，`sudo` 表示平台容器支持 sudo。`GET /me` 的 `permissions.connect_containers` 表示管理员接入权限。

管理员通过自己的公钥，以目标容器的 `connection.user` 登录；公钥保存在本人 `/keys` 下。管理员启用公钥会同步到全部运行容器，停用公钥、停用账号或撤销管理员角色会撤销后续新连接的跨用户接入权限。普通用户公钥同步到本人容器。`PATCH/DELETE /keys/{id}` 返回 `applies_to_running_tasks: true`；更新经同步进程与 Secret 挂载传播生效，已有 SSH 会话继续运行。

## Agent skill

将 ZIP 中的 `gpu-platform/` 放入 Agent 的 skill 目录。Codex 使用 `~/.codex/skills/gpu-platform/`。其他支持 SKILL.md 的 Agent 可安装同一目录。源码位于 [skills/gpu-platform](../skills/gpu-platform)。

执行环境配置 `GPU_PLATFORM_URL` 和 `GPU_PLATFORM_TOKEN`，随后可以要求 Agent「申请明天 14 点到 16 点的开发环境，2 核、4 GiB、无 GPU」。Agent 会查询资源、镜像与公钥，再提交申请，待运行后返回 SSH 命令和 config 配置。

skill 内的 Python 3 客户端仅使用标准库：

```bash
python3 scripts/client.py request GET /api/v1/me
python3 scripts/client.py request POST /api/v1/tasks --json @request.json
python3 scripts/client.py wait TASK_UUID --timeout 300
```

脚本路径相对 skill 目录。POST 会自动携带幂等 key；连接中断时错误结果保留该 key，重试使用 `--idempotency-key KEY` 和原参数。待审核和未来预约会立即报告状态。

开发环境使用 `mode=interactive`，配置起止日期；运行程序使用 `mode=batch`，填写命令并省略 `ends_at`。可指定镜像或本人运行中的开发环境（可带 GPU）。`POST /tasks/{id}/save` 可保存任何本人运行中的开发环境。

使用开发环境时，`workspace_gib` 可修改，省略则采用源工作卷的容量。批准后平台保存镜像、停止源容器，容量相同时复用原工作卷，容量变化时创建目标容量的新卷并离线复制。复制保留文件、权限与链接，成功后回收原卷；目标空间不足或复制失败时原卷保留。新程序始终沿用本人的个人卷。任务 `preparing` 状态同时包含保存镜像和准备工作卷；卷状态 `copy_pending` / `copying` 表示复制等待或执行中。

`GET /resources` 的 `request_capacity` 与申请页共用，包含 `fresh`、`cpu`、`memory_gib`、`storage_gib`、`workspace_gib`、`gpus` 和 `gpu_quota`。逐卡提供 `compute_percent`、`memory_gib`、`memory_total_gib` 与 `ready`。过期观测以 `fresh:false` 标记，CPU/内存为 `null`，逐卡余量归零；工作卷额度为 `null` 表示不限额。网页每 15 秒刷新。

完整操作示例见 [skill 接口参考](../skills/gpu-platform/references/api.md)。

Agent 可直接执行 `python3 scripts/client.py request GET /api/v1/openapi.json` 读取当前接口文档。文档公开可读，业务接口按令牌权限执行。

不限额用户组只返回基本信息和 `unlimited: true`，GPU、CPU、内存、存储、并发及环境时长的配额字段不保存数值，也不出现在响应中。关闭不限额时需重新设置完整额度。

## 动态 GPU 与多卡

`GET /api/v1/gpus` 返回物理 GPU 的 `uuid`、`index`、`sm_count`、`usable_memory_mib`、`ready`、已分配算力/显存与剩余量。`request_limits` 返回当前用户的单卡上限 `compute_per_card_percent`、`memory_per_card_gib`，总上限 `compute_total_percent`、`memory_total_gib`，以及可用设备的整 GiB 容量和 SM 数；不限额组的总上限为 `null`。`GET /resources` 同时返回 `gpus` 列表和总资源统计。

`POST /tasks` 使用 `gpu_requests` 数组逐卡指定算力和显存。第一个元素对应容器 `cuda:0`，第二个对应 `cuda:1`，内层 Docker 的 `--gpus all` 保持同一顺序。例如第一张全卡、第二张半卡：

```json
{"purpose":"混合 GPU 程序","mode":"batch","gpu_requests":[{"compute_percent":100,"memory_gib":8},{"compute_percent":50,"memory_gib":4}],"command":"./run.sh"}
```

`compute_percent` 为 2–100 的整数，`memory_gib` 为正整数。`gpu` 可省略，由数组长度确定；显式填写时须与数组长度一致。只填写卡数时，各行默认 100% 算力、8 GiB 显存。无 GPU 使用 `gpu=0`、`gpu_requests=[]`。

这两个字段也可独立填写字符串 `"max"`，对应网页各输入框旁的“最大”。提交时保留所有手填值，再将剩余总额度均分给选择最大的行，并逐卡受用户组上限和物理容量约束；整数余量优先分给前面的行。不限额组可申请整卡算力和扣除系统预留后的整 GiB 显存。例如总额 200% / 48 GiB、四卡均选最大时，每卡得到 50% / 12 GiB。返回和保存的 `gpu_requests` 使用解析后的整数；后续审批、排队和运行沿用这些数值。计算最大值使用设备容量和账号权限，当前占用由队列处理；无法满足最小额度或缺少设备信息时返回 400。

```json
{"purpose":"最大资源程序","mode":"batch","gpu_requests":[{"compute_percent":100,"memory_gib":"max"},{"compute_percent":"max","memory_gib":4}],"command":"./run.sh"}
```

平台为每行匹配不同物理卡，一次满足全部行后启动。`GET /tasks/{id}` 和任务列表返回 `gpu_requests`、`gpu_compute_total`（各卡算力百分比之和）、`gpu_memory_total_gib`（各卡显存之和）与 `gpu_allocations`。每条实际分配包含 `slot`（从 0 开始的配置行/CUDA 序号）、`uuid`、物理 `index`、`sm_count`、`compute_percent`、`actual_compute_percent`、`memory_mib`。`POST /tasks/{id}/stop` 释放全部关联卡。`GET /timeline` 的逐卡分配块包含 `gpu_slot`、`compute_percent`、`memory_gib`。

用户组管理接口使用 `gpu_compute_percent`（总算力百分比之和）、`gpu_memory_gib`（总显存 GiB）、`gpu_compute_per_card_percent`（单卡算力上限，0–100）和 `gpu_memory_per_card_gib`（单卡显存上限 GiB）。例如总算力 75、总显存 6、单卡算力 50、单卡显存 4，允许同时使用 50% / 4 GiB 与 25% / 2 GiB；单卡 75% 或 5 GiB 会被拒绝。`GET /me` 的 usage 包含 `gpu_compute_percent` 和 `gpu_memory_gib`。不限额组继续省略额度字段。
