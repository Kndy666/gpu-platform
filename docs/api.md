# API 与 Agent

API 根路径为 `/api/v1`，网页地址由 `config.yaml` 的 `platform.https_origin` 配置。网页“API 访问”管理个人令牌，并提供 [OpenAPI 地址](#接口入口)和 Agent skill 下载。

## 接口入口

| 入口 | 路径 |
| --- | --- |
| 网页令牌管理 | `/api/` |
| API | `/api/v1/` |
| OpenAPI 3.0 | `/api/v1/openapi.json` |
| Agent skill | `/api/v1/skill.zip` |

业务请求使用 `Authorization: Bearer TOKEN` 和 JSON 正文。令牌可选择 1 天、7 天、30 天、90 天、1 年或永久，默认 7 天。API 的 `expires_in` 使用秒数，`0` 表示永久，对应 `expires_at: null`。

令牌列表返回名称、有效期等元数据，详情接口提供本人令牌原文。删除令牌立即撤销认证；账号停用或密码变更后，已有令牌失效。

成功响应为 `{"data":...}`，错误响应为 `{"error":{"code":...,"message":...,"fields":...}}`。列表使用 `page`、`page_size` 分页，每页最多 100 条。时间采用带时区的 ISO 8601。

## 操作

以下路径均以 `/api/v1` 开头。网页与 API 共用账号角色、配额、表单和服务层，`GET /me` 返回当前权限与额度。普通用户访问本人资源；管理员管理全部任务、镜像和数据卷。公钥、令牌、个人构建与保存记录按所属账号访问。

| 功能 | 方法与路径 |
| --- | --- |
| 执行节点 | `GET/POST /nodes`，`GET/PATCH /nodes/{id}`；写入使用管理员权限 |
| 签发与撤销当前令牌 | `POST/DELETE /auth/token` |
| 个人令牌 | `GET/POST /auth/tokens`，`GET/DELETE /auth/tokens/{id}` |
| 账号与密码 | `GET /me`，`POST /me/password` |
| 资源、逐卡 GPU、时间轴 | `GET /resources`，`GET /gpus`，`GET /timeline?date=YYYY-MM-DD` |
| SSH 公钥 | `GET/POST /keys`，`PATCH/DELETE /keys/{id}` |
| 开发环境与程序 | `GET/POST /tasks`，`GET /tasks/{id}` |
| 用户延期 | `GET/POST /tasks/{id}/extensions`，`POST /extensions/{id}/review`（管理员审批） |
| 连接与日志 | `GET /tasks/{id}/connection`，`GET /tasks/{id}/logs` |
| 停止与审批 | `POST /tasks/{id}/stop`，`POST /tasks/{id}/review` |
| 保存环境与记录 | `POST /tasks/{id}/save`，`GET /snapshots`，`GET /snapshots/{id}` |
| 镜像查询与回收 | `GET /images`，`GET/DELETE /images/{id}`，`POST /images/{id}/keep`，`POST /images/{id}/recycle` |
| Dockerfile 构建 | `GET/POST /builds`，`GET /builds/{id}` |
| 数据卷 | `GET /volumes`，`GET/DELETE /volumes/{id}`；删除使用管理员权限 |
| 公用目录 | `GET /datasets`，`GET /datasets/download` |
| 管理概览与容器 | `GET /admin/overview`，`GET /admin/containers` |
| 审批与微信推送 | `GET /admin/approvals`，`GET/PATCH /admin/notifications` |
| 节点实时性能 | `GET /admin/performance?node=ID`；管理员可读 CPU 使用率、温度、内存、逐卡 GPU、磁盘和网络速率 |
| 成员 | `GET/POST /admin/members`，`GET/PATCH /admin/members/{id}` |
| 用户组 | `GET/POST /admin/groups`，`GET/PATCH/DELETE /admin/groups/{id}` |
| 存储、镜像、审计 | `GET /admin/storage`，`GET /admin/images`，`GET /admin/audits` |
| 保留设置 | `GET/PATCH /admin/settings` |

各接口的字段、查询参数、权限和幂等支持以 OpenAPI 为准。支持 `Idempotency-Key` 的操作在文档中标注；相同用户、方法、路径、key 和参数重试返回原结果，修改参数时使用新 key。

## 提交任务

用户组和成员编辑使用相同的资源与审批设置字段。创建成员时将选中用户组的配置复制到成员，之后成员的 CPU、内存、GPU、存储、并发、时长、延期次数及审批方式独立保存。`resource_limits` 返回成员资源值，`effective_limits` 是生效配置；编辑成员使用与用户组相同的平铺字段。成员和组的 `approval_mode` 均为 manual 或 automatic，`extension_limit` 均为非负整数，页面直接编辑实际值。

`GET /admin/approvals` 分页返回待人工审批的新建任务，`?kind=extensions` 返回待审批延期，支持 node 筛选。审批方式在成员与用户组接口中设置；审批操作仍使用任务及延期的 review 接口。“审批与推送”页面集中显示待审申请和推送配置。

本人可通过 `POST /tasks/{id}/extensions` 提交 `ends_at` 和 `reason`。每个开发环境默认允许成功延期一次；用户组 `extension_limit` 可修改默认次数，成员的同名字段独立保存，0 时关闭。每次新增的时长上限等于新建开发环境的 `interactive_hours`。人工审批通过前保持原结束时间；拒绝不消耗次数；自动审批通过后立即更新截止时间。`extension_count` 返回已成功延期次数，管理员通过 `POST /extensions/{id}/review` 提交 action=approve/reject，拒绝需 reason。延期只更新运行容器的截止时间注解。

`GET/PATCH /admin/notifications` 配置 PushPlus 个人微信推送：enabled、写入用的 token、events 和 reminder_minutes。GET 只返回 token_configured，空 token 保留原密钥。可选事件在 available_events 中查询。推送通过独立后台队列发送，限制为最多每分钟 5 次请求、每天 200 次请求；失败自动重试，recent 返回发送状态。默认关闭，填写 Token 并启用后才开始入队。

先通过 `GET /nodes` 查询节点 ID、架构、在线状态和维护状态。任务与构建正文的 `node` 指定执行节点；任务、镜像、构建和卷响应包含 `node_id`、`node_name`。

`GET /tasks`、`/images`、`/builds`、`/snapshots`、`/volumes`、`/timeline` 和管理端的 `/admin/containers`、`/admin/storage`、`/admin/images` 支持 `?node=ID` 筛选，省略时返回所有节点。环境保存记录按源环境归属筛选，响应含 `node_id`、`node_name`。`/resources` 省略节点时汇总资源；`/gpus`、`/datasets` 和 `/datasets/download` 省略节点时选择本机。任务省略 `node` 时选本机；使用 `source_environment` 时自动沿用源环境节点。

`GET /resources` 的 `nodes[]` 与首页共用节点分组，包含节点状态、CPU、内存、逐卡 GPU 余量、可用 GPU 算力 `gpu_available` 和存储 `storage_tib`；`?node=ID` 同时筛选汇总值和节点分组。

`GET /timeline` 的 `groups[]` 按节点包含 `rows`（资源行）、`blocks`（分配条）、`entries`（任务汇总）、`queued`（排队任务）。`queued` 只列当天已到启动时间的等待任务，按优先级和提交时间排序；其他日期为空。未来预约位于 `entries`，`state=reserved`。普通用户查看其他人的任务时，标题匿名且 `id`、`url` 为 `null`；管理员可见完整任务信息。

镜像、个人卷、工作卷和公用目录归属节点。镜像选择与源环境须对应执行节点，账号配额跨节点汇总。管理员通过 `POST /nodes` 或 `PATCH /nodes/{id}` 管理 `name`、`transport`、`ssh_port`、`ssh_user`、`identity`、`architecture`、`enabled` 和 `maintenance`；主机标识使用固定的 `local`。

开发环境使用 `mode=interactive`，填写 `starts_at` 和 `ends_at`。程序使用 `mode=batch`，填写 `command`，运行至命令结束。`image` 选择镜像，`source_environment` 选择本人运行中的开发环境，支持带 GPU 的环境。

```json
{"purpose":"运行程序","mode":"batch","source_environment":"开发任务UUID","gpu_requests":[{"compute_percent":100,"memory_gib":8},{"compute_percent":50,"memory_gib":4}],"cpu":8,"memory_gib":16,"workspace_gib":40,"command":"python /workspace/main.py"}
```

使用开发环境时，`workspace_gib` 省略则沿用源容量。获批后保存镜像并释放源容器；相同容量复用原工作卷，容量变化时创建新卷并离线复制。复制完成后回收原卷，失败时保留原数据。`preparing` 表示准备镜像或工作卷，卷状态 `copy_pending`、`copying` 表示等待或执行复制。

`POST /tasks/{id}/save` 主动保存运行中的开发环境，原容器继续运行。个人卷与工作卷独立保存。

## GPU 与余量

`gpu_requests` 每行对应一张物理 GPU，行顺序对应 CUDA 设备顺序。`compute_percent` 为 2–100 的整数，`memory_gib` 为正整数。两者都支持字符串 `"max"`：保留手填值，按组额度和单卡容量均分剩余资源。返回与保存的配置使用解析后的整数。

`gpu` 可省略，由数组长度确定；同时填写时与数组长度一致。只填卡数时，每卡默认 100% 算力、8 GiB 显存。CPU 环境使用 `gpu: 0`、`gpu_requests: []`。

| 数据 | 字段 |
| --- | --- |
| 逐卡余量 | `GET /gpus` 的 `devices`、`fresh` |
| 申请解析上限 | `GET /gpus` 的 `request_limits` |
| 申请页余量 | `GET /resources` 的 `request_capacity`：CPU、内存、存储、工作卷额度与逐卡 GPU |
| 任务实际分配 | `gpu_allocations`：slot、uuid、index、sm_count、实际算力与显存 |
| 用户组权限 | gpu_compute_percent、gpu_memory_gib、gpu_compute_per_card_percent、gpu_memory_per_card_gib |

不限额用户组返回 `unlimited: true`，配额字段省略；关闭不限额时填写完整额度。余量观测过期时 `fresh: false`，CPU、内存为 `null`，逐卡可用量归零。工作卷可用额度为 `null` 表示不限额。

## 管理员 SSH

管理页面的“性能”按节点展示实时硬件数据。`GET /admin/performance` 返回 `nodes[]`，省略 `node` 时包含全部节点。每项包含 `fresh`、`sampled_at` 和 `metrics`；`metrics.cpu` 提供使用率、核心数、负载、最高 CPU 温度 `temperature_c` 及传感器列表，未检测到 CPU 传感器时温度为 `null`。`gpus` 提供实际利用率、显存、温度、功耗；`disks` 为实际磁盘占用，`network` 的上下行单位为 MiB/s。这些指标由独立主机采集服务每 5 秒采样，经现有节点同步写入观测记录；超过 30 秒或节点离线时 `fresh=false`，已有采样保留并标记过期。网页刷新间隔可手动输入秒数，默认 5 秒，支持暂停和立即刷新。

`GET /admin/containers` 按 `q`、`status`、`mode`、`node` 和分页查询全体容器。`GET /tasks/{id}/connection` 返回 SSH `command` 与 `config`，管理员响应包含 `administrator_access`：本人启用公钥的元数据、`ready` 状态和 sudo 能力。

管理员使用本人密钥，以 `connection.user` 登录目标容器。公钥与角色变更同步至运行容器；挂载传播完成后影响新连接，已有会话继续运行。

## Agent skill

解压下载包，将 `gpu-platform/` 放入 Agent 的 skill 目录。Codex 使用 `~/.codex/skills/gpu-platform/`。源码位于 [skills/gpu-platform](../skills/gpu-platform)。

执行环境配置 `GPU_PLATFORM_URL` 和 `GPU_PLATFORM_TOKEN`，在 skill 目录中运行：

```bash
python3 scripts/client.py request GET /api/v1/me
python3 scripts/client.py request GET /api/v1/openapi.json
python3 scripts/client.py request POST /api/v1/tasks --json @request.json
python3 scripts/client.py wait TASK_UUID --timeout 300
```

客户端使用 Python 3 标准库。POST 自动生成幂等 key，连接中断时错误结果保留该 key，重试使用 `--idempotency-key KEY` 和原参数。完整操作示例见 [skill 接口参考](../skills/gpu-platform/references/api.md)。
