# 网页与 API 对照

API 绑定访问令牌所属的当前账号。账号停用、密码变更、管理员角色和用户组额度变更，均在后续请求中生效。`GET /api/v1/me` 返回 `permissions` 和当前用户组额度。

以下 API 路径以 `/api/v1` 开头。

| 网页操作 | API | 权限及共用实现 |
|---|---|---|
| 登录、退出 | `POST /auth/token`、`DELETE /auth/token` | 账号凭据签发；撤销当前令牌 |
| 修改密码 | `POST /me/password` | 本人；PasswordChangeForm |
| 资源概览、申请页余量、运行和等待数量 | `GET /resources`、`GET /tasks` | 登录用户；resource_overview、request_capacity、个人任务查询；申请页每 15 秒刷新余量 |
| 按日期查看资源分配 | `GET /timeline?date=YYYY-MM-DD` | allocation_timeline；他人任务信息对普通用户隐藏 |
| 创建开发环境、预约 | `POST /tasks`，`mode=interactive` | 本人；RequestForm、request_defaults、用户组及服务器资源校验 |
| 从镜像或开发环境运行程序 | `POST /tasks`，`mode=batch` | 本人镜像或共享镜像；源开发环境须属于本人、正在运行，可带 GPU；workspace_gib 可扩缩，变化时离线复制到新卷 |
| 每卡资源与多卡分配 | `GET /gpus`、`GET /resources`、`POST /tasks`、`GET /tasks/{id}` | 卡数、逐卡配置数组；各维度支持“最大”/`"max"`，提交时共用额度解析；实际分配包含配置行、物理编号、UUID、SM 和显存 |
| 开发环境与运行记录 | `GET /tasks?mode=interactive` 或 `batch` | 默认本人；`scope=all` 需要管理员 |
| 任务详情、状态、SSH 命令和 config | `GET /tasks/{id}`、`/connection` | 本人或管理员；tasks_for；返回关联卷和镜像信息 |
| 管理员容器列表与 SSH 接入 | `GET /admin/containers?q=...&status=...&mode=...`、`GET /tasks/{id}/connection` | 管理员；与网页共用 container_list；使用管理员本人公钥，以目标容器用户名登录，公钥和角色变化同步至运行容器 |
| 停止、取消 | `POST /tasks/{id}/stop` | 本人或管理员；request_stop；幂等重放先重新检查权限 |
| 批准、拒绝、优先级、延期 | `POST /tasks/{id}/review` | 管理员；ReviewForm、approve、reject、extend |
| 保存开发环境 | `POST /tasks/{id}/save` | 本人运行中的开发环境（可带 GPU） |
| 正式镜像、运行快照 | `GET /images?kind=permanent` 或 `temporary` | 与网页共用 image_catalog；临时快照属于本人 |
| Dockerfile/Git 构建、构建记录与日志 | `POST /builds`、`GET /builds`、`GET /builds/{id}` | 本人；ImageBuildForm；已有未结束构建时拒绝重复提交 |
| 保存记录 | `GET /snapshots`、`GET /snapshots/{id}` | 本人；管理员的个人镜像页也只显示自己的构建和保存记录 |
| 长期保留、安排回收 | `POST /images/{id}/keep`、`/recycle`；`DELETE /images/{id}` | 本人或管理员；image_actions_for、keep_image、recycle_image |
| 管理员查看和回收镜像 | `GET /admin/images`、`GET /images/{id}` 和上述回收接口 | 管理员；managed_image_list；返回总大小、待回收数和错误数 |
| 公钥添加、启用、停用 | `POST /keys`、`PATCH /keys/{id}`、`DELETE /keys/{id}` | 本人；SSHKeyForm；管理员也不能修改他人的公钥 |
| 生成、查看、复制、删除令牌 | `POST /auth/tokens`、`GET /auth/tokens`、`GET/DELETE /auth/tokens/{id}` | 本人；列表返回元数据，详情返回本人令牌原文；管理员不越过归属限制 |
| 成员搜索、创建、编辑、停用、重置密码和个人卷额度 | `GET/POST /admin/members`、`GET/PATCH /admin/members/{id}` | 管理员；member_list、MemberForm；授予管理员权限需要超级管理员 |
| 用户组增删改、默认组和成员转移 | `GET/POST /admin/groups`、`GET/PATCH/DELETE /admin/groups/{id}` | 管理员；GroupForm、DeleteGroupForm；返回成员数 |
| 存储池、数据卷和删除确认信息 | `GET /admin/storage`、`GET /volumes/{id}` | 管理员可查看所有卷；普通用户只能查看本人卷；返回池、成员、任务、占用和保留期 |
| 删除空闲数据卷 | `DELETE /volumes/{id}` | 管理员；request_volume_deletion；实际挂载和保存操作阻止删除 |
| 公用目录浏览、搜索、下载 | `GET /datasets`、`GET /datasets/download` | 登录用户；共用 dataset_content、open_dataset_path；上传使用 SSH |
| 管理概览和待审核列表 | `GET /admin/overview`、`GET /tasks?scope=all&status=submitted` | 管理员；全局查询；当前任务集合包含 approved |
| 审计记录 | `GET /admin/audits` | 管理员；audit_list、audit_presentation，含显示标签和字段说明 |
| 保留时间和访问配置 | `GET/PATCH /admin/settings` | 管理员；RecycleSettingsForm；地址、端口和时区只读 |

网页使用登录会话、表单和 HTML；API 使用 Bearer、JSON 和 ISO 8601 日期。两者按相同账号归属、角色、用户组额度和业务状态决定操作结果。管理员资源不限额组的 `unlimited` 标记只控制配额，管理权限取自账号角色。不限额组的配额列为空值，API 省略这些字段；切回限额组必须重新填写完整额度。

验证位于 `tests/integration/test_api_parity.py`，结合现有 API、门户、用户组、成员、工作流、数据集和卷管理测试，覆盖全部受保护 API 的认证、全部管理操作的角色限制、跨账号隔离、角色变更、配额变更，以及网页/API 的读写结果。运行方式见 [验证与自检](acceptance.md)。

用户组 GPU 权限在网页和 `/admin/groups` 使用相同四项：`gpu_compute_percent`、`gpu_memory_gib`、`gpu_compute_per_card_percent`、`gpu_memory_per_card_gib`。申请提交和审批校验单卡上限与申请总量，Kueue 用户队列限制同时占用的总算力和总显存。
