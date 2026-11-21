# API 与自动化

公开接口位于 `/api/v1`。Django Ninja 根据操作类型生成 `/api/v1/openapi.json`，交互文档位于 `/api/v1/docs`。面板和 HTTP 接口共同调用控制中心公开服务。

## 认证与请求

在面板“API 访问”创建具名令牌，或向 `POST /api/v1/auth/token` 提交当前认证模块接受的凭据。请求使用 `Authorization: Bearer TOKEN`，各操作检查账号归属和管理员权限。

```bash
curl -H "Authorization: Bearer $GPU_PLATFORM_TOKEN" \
  "$GPU_PLATFORM_URL/api/v1/me"
```

成功 JSON 响应使用 `data` 包装。错误返回 `error.code`、按语言显示的 `error.message` 和可选字段详情。自动化依据稳定的代码处理错误。API 默认英文；发送 `Accept-Language: zh-hans` 可获取中文提示。字段名、资源键、事件代码和标识符保持英文。

声明幂等性的变更操作接受 `Idempotency-Key`。传输失败后使用相同 key 和相同请求体重试。OpenAPI 列出各操作及其字段。

## 操作范围

| 分类 | `/api/v1` 下的路径 |
| --- | --- |
| 账号与令牌 | `/me`、`/me/password`、`/me/language`、`/auth/token`、`/auth/tokens` |
| 能力与余量 | `/nodes`、`/resources`、`/gpus`、`/timeline` |
| SSH 公钥 | `/keys`、`/keys/{id}` |
| 申请与执行 | `/tasks`、`/tasks/{id}`、`/tasks/{id}/connection`、`/tasks/{id}/logs`、`/tasks/{id}/stop` |
| 审批与延期 | `/tasks/{id}/review`、`/tasks/{id}/extensions`、`/extensions/{id}/review` |
| 镜像准备 | `/tasks/{id}/save`、`/snapshots`、`/builds`、`/images` |
| 存储与公共数据 | `/volumes`、`/datasets`、`/datasets/download` |
| 同步 | `/syncs`、`/images/{id}/sync`、`/admin/datasets/sync` |
| 管理 | `/admin/containers`、`/admin/approvals`、`/admin/members`、`/admin/groups`、`/admin/storage`、`/admin/images`、`/admin/audits`、`/admin/settings`、`/admin/performance`、`/admin/notifications` |

列表操作支持 OpenAPI 中列出的筛选和分页。节点绑定记录包含所属节点，资源响应包含已安装模块的能力扩展。提交申请前先查询目标节点能力。

程序请求选择节点与可访问镜像，也可提供源开发环境：

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

开发环境使用 `mode: interactive`、`starts_at`、`ends_at`。接口同时报告审批、准备与执行状态。提交成功后，读取任务状态和连接信息观察派发结果。

GPU 申请逐卡填写 `compute_percent` 与 `memory_gib`。默认模块将算力百分比换算为单卡 SM 份额；数组决定逻辑设备顺序，节点分配决定物理设备。标准 CPU、内存字段是登记资源键的 API 投影。自定义数量使用 `extensions.resources`；逐项分配使用 `extensions.allocations`，将组名映射为按资源键填写数量的列表。`extensions.volume_sizes` 可填写每个容器的工作卷容量；个人卷容量由成员设置决定，各节点分别创建。例如：`"extensions": {"resources": {"licenses": 2}, "allocations": {"accelerators": [{"accelerator_memory": 8}]}}`。提交前先从资源目录取得键、单位和分配元数据。

## 同步

`POST /images/{id}/sync` 接受目标节点和可选带宽限制。普通用户只能同步自己的私有镜像；管理员可同步共享镜像，目标副本自动保持共享。控制中心检查镜像访问权、CPU 架构、功能能力和总镜像配额后排队复制。`GET /syncs` 返回调用者可见的任务，包括传输字节数、状态，以及代码和参数组成的错误。

管理员向 `POST /admin/datasets/sync` 提交源节点、目标节点和相对目录范围。操作限定在配置的公共目录根路径内。镜像同步完成后生成独立镜像记录，执行自己的保留策略。

## Agent skill

英文 skill 位于 [skills/gpu-platform](../../skills/gpu-platform)，也可从 `/api/v1/skill.zip` 下载。设置 `GPU_PLATFORM_URL` 和 `GPU_PLATFORM_TOKEN` 后，使用其 Python 标准库客户端：

```bash
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/me
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/openapi.json
python3 skills/gpu-platform/scripts/client.py request POST /api/v1/tasks --json @request.json
python3 skills/gpu-platform/scripts/client.py wait TASK_UUID --timeout 300
```

请求失败时客户端保留幂等 key，重试可以沿用。完整命令示例与轮询方式见 [接口参考](../../skills/gpu-platform/references/api.md)。
