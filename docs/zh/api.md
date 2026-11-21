# 自动化操作

[首页](../../README.zh-CN.md) · [English](../en/api.md)

通过脚本或 Agent 使用与面板相同的账号、配额和审批规则。API 位于 `/api/v1`，部署中的 `/api/v1/docs` 和 `/api/v1/openapi.json` 列出了操作与已启用模块的字段。

## 连接平台

在面板的 API 访问页创建令牌，设置 `GPU_PLATFORM_URL` 和 `GPU_PLATFORM_TOKEN`，然后查看账号：

```bash
curl -H "Authorization: Bearer $GPU_PLATFORM_TOKEN" \
  "$GPU_PLATFORM_URL/api/v1/me"
```

通过 `/me.permissions` 查询权限，通过 `/nodes` 查询执行主机。`/resources`、`/gpus` 和 `/images?available=true` 支持 `?node=ID`，用于选择资源与镜像。

## 提交任务

例如，向 `POST /api/v1/tasks` 提交以下批处理请求，将节点和镜像 ID 换为部署中查询到的值：

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

开发环境使用 `mode: interactive`，并提供带时区的 `starts_at`、`ends_at`。批处理选择镜像或 `source_environment`，运行到命令退出。提交后读取任务状态，再查询连接信息或日志，跟踪审批、准备和执行进度。

| 需要做什么 | `/api/v1` 下的路径 |
| --- | --- |
| 管理账号、令牌或公钥 | `/me`、`/auth/tokens`、`/keys` |
| 查看容量和预约 | `/nodes`、`/resources`、`/gpus`、`/timeline` |
| 申请、连接或读日志 | `/tasks`、`/tasks/{id}/connection`、`/tasks/{id}/logs` |
| 停止、保存或延期环境 | `/tasks/{id}/stop`、`/tasks/{id}/save`、`/tasks/{id}/extensions` |
| 构建、查找或同步镜像 | `/builds`、`/images`、`/images/{id}/sync`、`/syncs` |
| 使用存储和公共数据 | `/volumes`、`/datasets`、`/datasets/download` |
| 审批申请与管理团队 | `/admin/approvals`、`/admin/members`、`/admin/groups`、`/admin/containers` |

GPU 请求逐卡填写 `compute_percent` 与 `memory_gib`。自定义资源使用 `extensions.resources`，逐项分配使用 `extensions.allocations`。从节点资源目录查询键、单位和限制；Ascend 在资源扩展中使用 `npu_count`。镜像同步要求 CPU 架构相同，成员复制自己的私有镜像，管理员还可复制共享镜像。私有副本计入所有者配额。

成功的 JSON 响应包含 `data`，错误包含 `error.code`、`error.message` 和可选字段详情。自动化依据代码处理错误，`Accept-Language: zh-hans` 选择中文提示。分页列表包含 `items`、`count`、`page`、`page_size`、`next_page`。

声明支持 `Idempotency-Key` 的操作在连接失败后使用相同 key 与请求体重试。方法、分页、筛选与必填字段以 OpenAPI 为准。

## 使用 Agent skill

下载 `/api/v1/skill.zip`，或使用 [skills/gpu-platform](../../skills/gpu-platform)。英文指令与 Python 标准库客户端覆盖资源申请、连接开发环境和授权的管理操作：

```bash
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/me
python3 skills/gpu-platform/scripts/client.py request POST /api/v1/tasks --json @request.json
python3 skills/gpu-platform/scripts/client.py wait TASK_UUID --timeout 300
```

请求失败时客户端保留幂等 key。[操作参考](../../skills/gpu-platform/references/api.md)说明了任务、镜像、存储与管理流程。
