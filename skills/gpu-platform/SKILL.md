---
name: gpu-platform
description: 通过计算资源平台 API 查询配额与资源、申请开发容器、运行程序、保存或构建镜像，并处理获授权的管理操作。用于操作已有平台资源。
---

使用平台 HTTP API 完成当前用户要求的资源操作。通过 `GPU_PLATFORM_URL` 配置目标站点，或给客户端传入 `--url`。地址缺失时先取得用户的部署地址。

先读取 `GPU_PLATFORM_TOKEN` 是否已配置，避免打印其值。用户在网页「API 访问」中生成令牌后交给执行环境；凭证缺失时请用户配置令牌。令牌权限随账号、用户组和启停状态生效。更改密码或撤销令牌后需要重新认证。

通过 `/me` 返回的 `permissions` 和用户组额度判断可用操作。管理角色变更即时生效；个人公钥、令牌、构建和保存记录始终按本人归属访问。

使用 [scripts/client.py](scripts/client.py) 调用 API，Python 3 标准库即可运行。例如：

```bash
python3 scripts/client.py request GET /api/v1/me
python3 scripts/client.py request GET /api/v1/resources
python3 scripts/client.py request GET /api/v1/images --query available=true
```

需要确认路径、HTTP 方法、参数或权限时，执行 `python3 scripts/client.py request GET /api/v1/openapi.json` 读取站点当前的 OpenAPI 文档，文档无需令牌。需要管理、存储或镜像示例时读取 [references/api.md](references/api.md)。相对脚本路径以本 skill 所在目录为基准。

申请开发环境时，先确认账号额度、可用镜像和已启用的 SSH 公钥。缺少公钥时向用户取得公钥，或按用户授权生成密钥后提交公钥。`mode=interactive` 表示按日期时段使用；默认无 GPU，申请中写明 CPU、内存和工作卷大小。时间使用带时区的 ISO 8601；用户口述日期以站点返回的 `timezone` 为准，当前为北京时间。

GPU 算力和显存可分别填写 `"max"`。服务端按当前用户组权限、物理容量和其他行手填值计算，将剩余总额度均分给选最大值的行，并返回确定的整数。`GET /gpus` 的 `request_limits` 给出解析使用的单卡上限、总上限和设备容量；不限额组总上限为 `null`。当前占用由排队处理。

GPU 申请使用 `gpu_requests` 数组，每个元素填写 `compute_percent`（2–100）和 `memory_gib`（正整数）。例如 `[ {"compute_percent":100,"memory_gib":8}, {"compute_percent":50,"memory_gib":4} ]` 表示第一张全卡、第二张半卡。卡数 `gpu` 可省略，由数组长度确定；显式提供时须一致。数组顺序对应外层与内层 Docker `--gpus all` 的 CUDA 设备顺序。通过 `GET /gpus` 查询逐卡余量。平台一次匹配全部物理卡后启动；`gpu_allocations.slot` 对应配置行，`index` 是实际物理 GPU 编号。用户组 `gpu_compute_percent` 是各卡及同时运行任务的总算力额度（百分比之和），`gpu_memory_gib` 是总显存；`gpu_compute_per_card_percent`、`gpu_memory_per_card_gib` 分别限制单卡。管理员不限额。运行命令和环境变量不覆盖 NVIDIA、CUDA_MPS 或平台保留配置。

运行程序使用 `mode=batch`，填写命令并选择 `image` 或自己运行中的 `source_environment`（可带 GPU）。管理员批准后，平台保存环境、释放源容器并排队。个人卷继续复用，`workspace_gib` 可调整：省略或相同时沿用原工作卷，变化时离线复制到新卷，完成后回收原卷；源开发环境在批准时仍须运行。Agent 等待目标任务状态。程序运行至命令结束，省略 `ends_at`。用户尚未提供命令时先完成开发环境申请。

创建任务、构建镜像和保存环境时使用可复用的 `Idempotency-Key`。客户端会为 POST 自动生成 key，连接中断会把 key 写入错误结果；重试同一操作时传回该 key 和相同参数。先查询任务状态，再决定后续动作。

提交后报告任务 ID 和审核状态。普通账号需要管理员批准；管理接口只用于本次用户授权的审核或维护操作。可用 `client.py wait TASK_ID --timeout 300` 等待连接就绪；未来预约或待审核时先向用户报告状态。超时后保留任务并报告当前状态。

运行后给用户返回 `connection.command` 与 `connection.config`，它们包含该容器的地址、端口、登录名和 HostKeyAlias。需要进度时查询任务或 `/tasks/{id}/logs`；容器释放后日志仍位于工作卷的 `/workspace/.platform/`。

获授权的管理员可用 `GET /admin/containers` 查询全体容器，再通过 `GET /tasks/{id}/connection` 获取接入配置。管理员使用本人账号下启用公钥对应的私钥，以响应中的容器用户名登录，进入后可 sudo；`administrator_access.ready` 表示已有启用公钥。公钥启停、账号启停和角色变化会同步至运行容器，挂载传播有延迟，已有 SSH 会话保持。

停止、回收镜像、删卷或调整成员与用户组时，按当前用户明确指定的对象与范围操作。卷删除由平台检查实际占用；镜像回收有保留期限。完整错误含 HTTP 状态和字段说明，按错误修正请求；权限不足时报告给用户。
