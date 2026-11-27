# API 与 Agent skill

[返回首页](../../README.zh-CN.md) · [English](../en/api.md)

`/api/v1` 提供完整的 HTTP JSON 接口，与网页控制台共用同一套账号、配额、权限与审批规则。访问部署地址的 `/api/v1/docs` 或 `/api/v1/openapi.json` 可查看实时接口文档与已启用模块字段。

## 认证方式

- **脚本与 AI Agent**：在**账号与访问 → API 与 Agent 技能**中创建访问令牌，请求头携带 `Authorization: Bearer <token>`。
- **浏览器控制台**：通过同源 Nginx 入口使用 HttpOnly Session Cookie（`GET /api/v1/auth/csrf`、`POST /api/v1/auth/session`、`DELETE /api/v1/auth/session`），写请求携带 `X-CSRFToken`。

使用 Bearer 令牌检查账号与权限：

```bash
curl -H "Authorization: Bearer $GPU_PLATFORM_TOKEN" \
  "$GPU_PLATFORM_URL/api/v1/me"
```

通过 `/me.permissions`、`/me.effective_limits` 和 `/nodes` 查询可用服务器；`/resources`、`/gpus` 和 `/images?available=true` 支持 `?node=ID` 过滤。

## 提交任务示例

向 `POST /api/v1/tasks` 提交批处理任务：

```json
{
  "purpose": "训练基线模型",
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

- **开发环境**使用 `mode: "interactive"` 并提供带时区的 `starts_at` 与 `ends_at`。
- **批处理任务**使用 `mode: "batch"`、`command` 以及 `image` 或运行中的 `source_environment`。
- **GPU 申请**在 `gpu_requests` 中按卡填写 `compute_percent` 和 `memory_gib`（均支持 `"max"`），或通过 `extensions.resources` 与 `extensions.allocations` 传递插槽原生字段（例如昇腾服务器的 `npu_count`）。同一次请求不要同时传 `gpu_requests` 与 `extensions.allocations.gpu`。

## 接口分组

以下路径均位于 `/api/v1` 下：

| 功能分组 | 路径 |
| --- | --- |
| 账号、令牌与 SSH 密钥 | `/me`、`/me/language`、`/me/password`、`/me/notifications`、`/auth/tokens`、`/keys` |
| 服务器、容量与时间表 | `/nodes`、`/resources`、`/gpus`、`/timeline`、`/request-options` |
| 任务、SSH 连接与日志 | `/tasks`、`/tasks/{id}`、`/tasks/{id}/connection`、`/tasks/{id}/logs` |
| 生命周期操作 | `/tasks/{id}/stop`、`/tasks/{id}/save`、`/tasks/{id}/extensions`、`/tasks/{id}/shorten` |
| 镜像、构建与数据同步 | `/images`、`/images/{id}`、`/builds`、`/snapshots`、`/images/{id}/sync`、`/syncs` |
| 存储与共享数据集 | `/volumes`、`/datasets`、`/datasets/download` |
| 站内通知 | `/inbox`、`/inbox/{id}`、`/inbox/{id}/read`、`/inbox/reminders` |
| 后台管理 | `/admin/approvals`、`/tasks/{id}/review`、`/extensions/{id}/review`、`/admin/containers`、`/admin/performance`、`/admin/performance/history`、`/admin/usage/live`、`/admin/usage/history`、`/admin/storage`、`/admin/images`、`/admin/members`、`/admin/groups`、`/admin/notices`、`/admin/notifications`、`/admin/settings`、`/admin/audits` |

`GET /images` 列出有权访问的永久镜像。设置 `available=true` 可只查看已经验证且可用于任务的镜像。

镜像所有者或管理员可通过 `DELETE /images/{id}` 请求删除闲置的私有永久镜像，请求无需提供正文。响应的 `data` 字段包含与 `GET /images/{id}` 相同的镜像记录。等待清理时返回 `202 Accepted`，且 `data.state` 为 `"deleting"`。清理已经完成时返回 `200 OK`，且 `data.state` 为 `"deleted"`。

## 响应结构与幂等重试

- 成功响应统一返回 `{"data": ...}`；分页接口包含 `items`、`count`、`page`、`page_size` 与 `next_page`。
- 错误响应统一返回 `{"error": {"code": "...", "message": "...", "fields": {...}}}`，自动化脚本应依据 `error.code` 分支判断。
- 请求头携带 `Accept-Language: zh-hans` 可返回简体中文提示。
- 声明支持 `Idempotency-Key` 的创建类接口在网络超时后使用相同密钥与请求体重试时，会返回已记录的结果，避免重复创建。

## 使用 Agent skill

从 `/api/v1/skill.zip` 下载或直接使用仓库中的 [skills/gpu-platform](../../skills/gpu-platform)。随附的 Python 标准库客户端自动处理 Bearer 认证、幂等键和任务就绪等待：

```bash
python3 skills/gpu-platform/scripts/client.py request GET /api/v1/me
python3 skills/gpu-platform/scripts/client.py request POST /api/v1/tasks --json @request.json
python3 skills/gpu-platform/scripts/client.py wait TASK_UUID --timeout 300
```

完整接口参数与运维流程见 [skills/gpu-platform/references/api.md](../../skills/gpu-platform/references/api.md)。
