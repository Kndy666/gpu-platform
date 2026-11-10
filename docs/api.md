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
| 连接与日志 | `GET /tasks/{id}/connection`，`GET /tasks/{id}/logs` |
| 停止与审批 | `POST /tasks/{id}/stop`，`POST /tasks/{id}/review` |
| 保存环境与记录 | `POST /tasks/{id}/save`，`GET /snapshots`，`GET /snapshots/{id}` |
| 镜像查询与回收 | `GET /images`，`GET/DELETE /images/{id}`，`POST /images/{id}/keep`，`POST /images/{id}/recycle` |
| Dockerfile 构建 | `GET/POST /builds`，`GET /builds/{id}` |
| 数据卷 | `GET /volumes`，`GET/DELETE /volumes/{id}`；删除使用管理员权限 |
| 公用目录 | `GET /datasets`，`GET /datasets/download` |
| 管理概览与容器 | `GET /admin/overview`，`GET /admin/containers` |
| 成员 | `GET/POST /admin/members`，`GET/PATCH /admin/members/{id}` |
| 用户组 | `GET/POST /admin/groups`，`GET/PATCH/DELETE /admin/groups/{id}` |
| 存储、镜像、审计 | `GET /admin/storage`，`GET /admin/images`，`GET /admin/audits` |
| 保留设置 | `GET/PATCH /admin/settings` |

各接口的字段、查询参数、权限和幂等支持以 OpenAPI 为准。支持 `Idempotency-Key` 的操作在文档中标注；相同用户、方法、路径、key 和参数重试返回原结果，修改参数时使用新 key。

## 提交任务

先通过 `GET /nodes` 查询节点 ID、架构、在线状态和维护状态。任务与构建正文的 `node` 指定执行节点；任务、镜像、构建和卷响应包含 `node_id`、`node_name`。

`GET /tasks`、`/images`、`/builds`、`/volumes`、`/timeline` 支持 `?node=ID` 筛选，省略时返回所有节点。`/resources` 省略节点时汇总资源；`/gpus`、`/datasets` 和 `/datasets/download` 省略节点时选择本机。任务省略 `node` 时选本机；使用 `source_environment` 时自动沿用源环境节点。

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

`GET /admin/containers` 按 `q`、`status`、`mode` 和分页查询全体容器。`GET /tasks/{id}/connection` 返回 SSH `command` 与 `config`，管理员响应包含 `administrator_access`：本人启用公钥的元数据、`ready` 状态和 sudo 能力。

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
