# API 操作

站点根地址使用 `GPU_PLATFORM_URL`，令牌使用 `GPU_PLATFORM_TOKEN`。请求正文为 JSON，认证为 `Authorization: Bearer …`。JSON 成功响应为 `{"data": ...}`，失败为 `{"error":{"code":...,"message":...,"fields":...}}`。列表的 `data` 包含 `items`、`count`、`page`、`page_size` 和 `next_page`；每页最多 100 条。

`GET /api/v1/openapi.json` 是当前部署的完整契约。以下路径均以 `/api/v1` 开头，末尾无斜杠。

| 路径 | 方法 | 功能 |
|---|---|---|
| `/auth/token` | POST / DELETE | 用户名密码换令牌 / 撤销当前令牌 |
| `/auth/tokens` | GET / POST | 查看本人令牌元数据 / 以当前账号生成令牌，字段 name、expires_in |
| `/auth/tokens/{id}` | GET / DELETE | 查看本人令牌原文 / 删除指定令牌 |
| `/me` | GET | 账号、用户组、配额、当前占用 |
| `/nodes`、`/nodes/{id}` | GET | 节点 ID、架构、在线状态、维护状态 |
| `/me/password` | POST | 使用 old_password、new_password1、new_password2 修改密码 |
| `/resources` | GET | 资源余量、nodes 节点分组、默认镜像、服务器时间与时区 |
| `/timeline?date=YYYY-MM-DD` | GET | groups 按节点返回运行、预约及排队任务 |
| `/keys` | GET / POST | 查看或添加公钥，字段 name、public_key |
| `/keys/{id}` | PATCH / DELETE | 修改名称或 enabled / 停用公钥 |
| `/tasks` | GET / POST | 列表或申请资源；管理员可 scope=all |
| `/tasks/{id}` | GET | 状态、配额、退出码、错误和连接信息 |
| `/tasks/{id}/connection` | GET | SSH 命令与 config |
| `/tasks/{id}/logs?tail=200` | GET | 当前容器日志，最多 3000 行 / 250 KB |
| `/tasks/{id}/stop` | POST | 取消或停止，正文 `{}` |
| `/tasks/{id}/save` | POST | 保存本人运行中的开发环境（可带 GPU），正文 `{}` |
| `/snapshots`、`/snapshots/{id}` | GET | 保存操作及其产物 artifact_id |
| `/images?available=true`、`/images/{id}` | GET | 可用镜像或镜像详情；kind=permanent/temporary 对应网页镜像分区 |
| `/images/{id}/keep`、`/images/{id}/recycle` | POST | 长期保留 / 安排回收 |
| `/images/{id}` | DELETE | 安排镜像回收 |
| `/builds` | GET / POST | 构建列表或提交 name、dockerfile、可选 context_git |
| `/builds/{id}` | GET | 构建状态、日志、产物 artifact_id |
| `/volumes`、`/volumes/{id}` | GET | 本人的个人卷和工作卷 |
| `/volumes/{id}` | DELETE | 管理员删除空闲卷 |
| `/datasets?path=...&q=...` | GET | 浏览、搜索公用目录 |
| `/datasets/download?path=...` | GET | 下载公用文件；客户端用 --output 保存 |

开发环境示例：

```json
{"mode":"interactive","purpose":"环境调试","gpu":0,"cpu":2,"memory_gib":4,"workspace_gib":20,"starts_at":"2026-11-04T14:00:00+08:00","ends_at":"2026-11-04T16:00:00+08:00"}
```

示例日期需按用户实际要求替换。省略 image 会使用共享默认镜像。图形界面的 GiB 与 API 字段 memory_gib、workspace_gib 一致。

任务和构建正文的 `node` 是执行节点数字 ID，省略时选本机；从 `source_environment` 运行时自动沿用源节点。资源、GPU、镜像、任务、构建、环境保存记录、卷、时间轴及公用目录支持 `?node=ID`。列表省略节点时跨节点返回；GPU 和公用目录省略时选本机。镜像、个人卷、工作卷和公用目录归属节点，任务、镜像、构建、保存记录和卷等响应包含 `node_id`、`node_name`。账号配额跨节点计算。

`/resources` 的 `nodes[]` 与首页节点卡片共用数据：`id`、`name`、`state`、`state_label`、`resources`（CPU 和内存）、`gpus`（逐卡可用算力与显存）、`gpu_available`（可用 GPU 算力总和，以整卡为单位）、`storage_tib`。`?node=ID` 同时筛选汇总值和 `nodes`。

`/timeline` 的 `groups[]` 包含节点 `id`、`name`、`gpu_count`，以及 `rows` 资源行、`blocks` 分配条、`entries` 任务汇总、`queued` 排队任务。`queued` 按优先级和提交时间排列，包含 `id`、`title`、`url`、`status`、`status_label`、`gpu`、`cpu`、`memory_gib`；它只列当天已到启动时间的等待任务，查看其他日期时为空。未来预约见 `entries` 的 `state=reserved`。普通用户查看其他人的任务时，`id`、`url` 为 `null`，标题匿名；管理员可见完整任务信息。

```bash
python3 scripts/client.py request GET /api/v1/nodes
python3 scripts/client.py request GET /api/v1/resources --query node=3
python3 scripts/client.py request GET /api/v1/timeline --query node=3
python3 scripts/client.py request GET /api/v1/datasets --query node=3 --query q=数据集名称
```

示例节点 ID 以 `/nodes` 返回的实际值为准。

从已配置的开发容器运行：

```json
{"mode":"batch","purpose":"运行实验","source_environment":"开发任务UUID","gpu":1,"cpu":8,"memory_gib":16,"command":"python /workspace/main.py","working_dir":"/workspace","environment":{"OMP_NUM_THREADS":"8"}}
```

使用工作镜像时将 source_environment 换成 image UUID。环境变量值为单行字符串，平台保留的 HOME、PATH、TASK_ 等变量由服务端校验。批任务命令结束后释放计算资源，工作卷按保留设置保留。

# 管理操作

需要管理员账号；普通账号收到 403。现有管理员保护规则、组配额和卷占用规则同网页生效。

`/me.permissions` 表示当前管理权限。不限额组只改变配额。管理员可管理他人任务、镜像和数据卷；公钥、令牌、构建记录和保存记录保持本人可见。

- `GET /admin/overview`：管理计数。
- `POST /nodes`、`PATCH /nodes/{id}`：登记或修改 name、transport、ssh_port、ssh_user、identity、architecture、enabled、maintenance；identity 为主控密钥目录内的私钥文件名。维护模式暂停新任务派发，保留已有任务同步。
- `GET /admin/containers?q=...&status=...&mode=...&node=ID`：全体容器，可按名称、所属用户、容器编号、状态和节点筛选；管理员通过 `GET /tasks/{id}/connection` 的 command/config，以目标容器用户名和本人密钥登录。`administrator_access` 返回本人启用公钥的元数据与 ready 状态；可在容器内 sudo。
- `POST /tasks/{id}/review`：action 为 approve、reject、stop、extend；priority 为 0 或 100，拒绝带 reason，延期带 ends_at 和 reason。
- `GET/POST /admin/members`，`GET/PATCH /admin/members/{id}`：账号创建与编辑。group 为用户组数字 ID，home_quota_gib 为个人卷额度。新账号需要 username、password；停用账号使用 is_active=false。
- `GET/POST /admin/groups`，`GET/PATCH/DELETE /admin/groups/{id}`：用户组。unlimited=true 表示不限组额度，此时清空并省略全部配额字段；改回限额组时须填写完整额度。make_default=true 设置默认组。删除有成员的组时用 JSON `{"destination":目标组ID}` 转移成员。
- `GET /admin/storage?node=ID`：节点的存储池和使用中的卷，存储池含 node_id、node_name；省略节点时返回全部。
- `GET /admin/images?node=ID`：节点内尚未回收的镜像及容量统计；省略节点时返回全部。
- `GET /admin/audits?action=...`：审计事件。
- `GET/PATCH /admin/settings`：workspace_retention_days、snapshot_hours、failed_snapshot_hours。地址和端口范围只读。

与网页一致的展示数据：`/resources` 含 running_count、queued_count；`/admin/groups` 含 member_count；卷数据含 pool_id、pool_name、username、request_name；`/admin/images` 的 summary 含 ready_bytes、waiting_count、error_count。任务详情含关联工作卷、源环境名称和 SSH 配置。成员搜索 q 同时匹配姓名、用户名和邮箱。

创建类请求支持 Idempotency-Key；服务端在 OpenAPI 中标出支持该头的操作。同一用户、方法、路径、key 和请求参数重试会返回原结果。更换参数需新 key。

状态：submitted 待审核，approved 已批准，scheduled 预约，preparing 准备环境，queued 排队，starting 启动，running 运行；succeeded、failed、canceled、expired、cleaned、rejected 为结束状态。connection 在运行且 SSH 端口可用时才有值。

SSH 公钥启停会同步到现有运行容器。管理员启用公钥允许接入全部用户容器；普通用户公钥用于本人容器。账号停用和管理员降级也参与同步。更新有 Secret 挂载传播延迟，影响后续新连接，已有 SSH 会话保持。公用目录上传沿用服务器 SSH。签发令牌时可传 expires_in（整数秒数，默认 604800，即 7 天）；0 表示永久，此时 expires_at 为 null。网页提供有效期选项，客户端登录可用 `login --username 用户名 --expires-in 0`。可 DELETE /auth/token 撤销；改密码和停用账号也会使令牌失效。

## Docker 环境

需要 Docker 时，在 `/images` 中选择共享镜像“Ubuntu 22.04”。平台启动独立 Rootless Docker，支持前台 `docker run --rm --gpus all IMAGE COMMAND` 作为运行命令；`all` 使用外层任务获分配的 GPU。内层镜像和 Docker 卷位于 `/workspace/.docker`，工作卷保留期对这些数据同样生效。长期使用的内层镜像可推送到自己的外部仓库，或使用 `docker save` 保存到个人卷。平台仓库在容器内提供只读凭据，镜像发布使用网页/API 构建与保存。从开发环境提交运行时，相同容量复用工作卷，容量变化时复制到新卷；使用已有镜像直接申请时创建新工作卷。

GPU：`GET /gpus` 查询逐卡余量及当前用户的 `request_limits`；`POST /tasks` 通过 `gpu_requests` 数组逐卡申请，如 `[{"compute_percent":100,"memory_gib":8},{"compute_percent":50,"memory_gib":4}]`。每个字段可独立用字符串 `"max"` 取最大值：保留手填值，将剩余总额度均分给选最大值的行，并受单卡权限和硬件容量限制；响应返回确定的整数。`gpu` 可省略；配置行对应 CUDA 设备序号，实际物理编号见 `gpu_allocations` 的 `slot`、`uuid`、`index`。用户组 GPU 权限包含总算力 `gpu_compute_percent`、总显存 `gpu_memory_gib`、单卡算力上限 `gpu_compute_per_card_percent` 与单卡显存上限 `gpu_memory_per_card_gib`。

申请页的余量对应 `GET /resources` 的 `request_capacity`。从带 GPU 或无 GPU 的开发环境提交程序均可使用 `source_environment`；`workspace_gib` 省略时沿用源大小，填写不同容量时新建卷并复制，目标需能容纳原数据。个人卷复用，原工作卷在复制成功后回收。
