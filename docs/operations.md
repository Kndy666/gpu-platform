# 使用与维护

网页地址由 `config.yaml` 的 `platform.https_origin` 配置。日期与时段按北京时间显示。

## 开发环境

1. 在“SSH 公钥”添加设备公钥。
2. 打开“新建开发环境”，选择执行节点，查看该节点的 GPU、CPU、内存与存储余量。
3. 选择镜像、日期时段和资源。选择 GPU 后逐行填写每张卡的算力与显存。
4. 提交申请，管理员批准后按时段启动。
5. 打开任务详情，复制 SSH 命令或 `~/.ssh/config` 配置连接；VS Code 使用同一配置。

默认镜像 Ubuntu 22.04 包含 SSH、sudo、网络与编译工具、bubblewrap、htop、Bash 高亮和 Docker。容器用户名与网站用户名一致，个人目录为 `/home/<用户名>`。

可使用 `sudo apt update`、`sudo apt install python3 python3-pip` 安装软件，也可在个人目录安装 Python、Conda 和框架。APT 使用 Ubuntu 官方源，pip 与 Conda 使用各自默认源。

开发环境按绝对日期时段运行，到点释放计算资源。排队时间计入所选时段，延期由管理员在任务详情调整结束时间。列表中的停止图标和详情中的停止按钮用于提前结束任务。

## 运行程序

“运行程序”填写 Shell 命令、工作目录、环境变量和资源。程序运行至主命令结束，退出码 0 记录成功，其他退出码记录失败。SSH 断开后主命令继续执行。日志保存在 `/workspace/.platform/`。

| 来源 | 环境文件 | 个人卷 | 工作卷 | 源容器 |
| --- | --- | --- | --- | --- |
| 使用镜像 | 选中镜像的版本 | 本人的个人卷 | 新建 | 继续运行 |
| 使用开发环境 | 保存当前容器可写层 | 复用 | 同容量复用，变化时复制到新卷 | 镜像保存成功后停止 |

来源可选任意本人运行中的开发环境，包括带 GPU 的环境。管理员批准后，平台保存镜像、释放源容器，再准备数据卷和运行任务。保存失败时源环境继续按原时段运行。

使用开发环境时，工作卷容量可以调整。平台为新容量创建卷，离线复制文件、权限、链接和稀疏文件，写入完成并同步后回收原卷。复制期间需要目标卷所占的磁盘空间；目标容量需容纳现有数据，失败时保留原卷。

需要虚拟环境时，在运行命令中激活或使用解释器完整路径：

```bash
source "$HOME/venvs/app/bin/activate"
python /workspace/main.py
```

## 镜像

开发环境详情的“保存镜像”保存容器系统文件，原环境继续运行。个人卷和工作卷独立保存。后续用该镜像申请环境时，挂载本人的个人卷并创建新工作卷。

“环境镜像”支持选择节点，填写名称、Dockerfile 和可选 HTTPS Git 上下文。构建结果进入该节点本人的镜像仓库，页面提供使用入口、节点、架构、镜像地址和构建日志。基础镜像使用目标节点的 ARM64 或 AMD64 架构。

每用户同时运行一份构建，构建上限 1 小时、4 核 CPU、8 GiB 内存。构建完成后保留 Job/Pod 24 小时，日志读取上限为 3000 行、250 KB。

| 镜像来源 | 默认保留时间 |
| --- | --- |
| 主动保存、Dockerfile 构建 | 长期 |
| 从开发环境运行程序时自动保存 | 全部引用任务结束并释放资源后计时：成功或取消 24 小时，失败 72 小时 |

自动保存的镜像位于“运行快照”。“长期保留”将其转为普通个人镜像；“回收”安排闲置镜像在 24 小时后删除，期间可撤销。共享基础镜像长期保留。

有新任务引用快照时，保留期在全部引用释放后重新计算。仓库删除标签后，按 1 小时间隔清理超过 1 小时的无引用数据层。镜像删除失败每 5 分钟重试，管理页显示状态。

## 容器内 Docker

默认镜像提供独立 Rootless Docker 引擎，可使用 `docker build`、`docker compose` 和：

```bash
docker run --rm --gpus all -v /workspace:/workspace -w /workspace IMAGE COMMAND
```

`all` 使用当前任务分配的 GPU，CUDA 顺序与申请表格一致。内外层所有进程共用任务的 CPU、内存、SM 分区和逐卡显存额度。程序任务使用前台 Docker 命令，平台按该命令的退出码记录结果。

Docker 镜像、缓存、容器可写层与命名卷位于 `/workspace/.docker`，日志位于 `/workspace/.platform/docker.log`。这些数据随工作卷保留。需要长期保存的内层镜像可推送到自己的外部仓库，或用 `docker save` 存入个人卷。

容器内的个人仓库身份支持拉取镜像，平台镜像发布使用网页或 API 的构建与保存操作。`docker run -p` 将端口发布到开发环境，可通过 SSH 本地转发访问。

## 管理

| 页面 | 操作 |
| --- | --- |
| 节点 | 登记反向 SSH 端口与专属密钥文件名，查看在线状态、最近同步与错误，启停同步或切换维护模式 |
| 审批 | 查看资源、日期与来源，批准、拒绝、调整优先级或延期 |
| 成员 | 创建账号、重置密码、启停账号、配置角色、用户组和个人卷容量 |
| 用户组 | 新增、编辑、删除、设置默认组，配置资源上限 |
| 容器 | 搜索全体用户容器，打开详情和 SSH 连接 |
| 存储 | 查看存储池与数据卷，删除空闲卷 |
| 镜像 | 查看保留与回收状态，处理镜像保留 |
| 设置 | 配置工作卷与运行快照的保留时长 |
| 审计 | 查看审批、停止、保存、回收等操作 |
| 公用目录 | 浏览、搜索和下载公共文件 |

用户组的 GPU 权限包括总算力、总显存、单卡算力和单卡显存上限。算力单位为百分点，例如 150 表示一张全卡加一张半卡的总份额。其余额度包括 CPU、内存、存储、镜像、并发数和开发时长。

“资源不限额”清空组配额，关闭后重新填写额度。删除含成员的组时选择迁入组。管理员角色与资源额度独立配置。

管理员 SSH 接入时，先在自己的“SSH 公钥”添加公钥，再从“管理 → 容器”复制目标连接配置。认证使用管理员自己的密钥，登录名使用目标容器用户名，进入后可执行 `sudo -i`。公钥和角色变更同步到运行容器，挂载传播完成后影响新连接。

## 数据与回收

| 数据 | 位置 | 保存方式 |
| --- | --- | --- |
| 个人文件 | `/home/<用户名>` | 同账号在同节点的任务共享，长期保留 |
| 任务文件 | `/workspace` | 结束后默认保留 3 天，到期前一天通知 |
| 内层 Docker | `/workspace/.docker` | 随工作卷保留 |
| 公用目录 | 主机 `storage.datasets`，容器 `/datasets` | 管理员通过服务器 SSH 上传，网页和容器只读 |

“管理 → 设置”修改的保留时间用于之后结束的任务或开始计时的快照。“管理 → 存储”删除卷时显示名称、容量和所属用户；平台等待任务、保存操作和实际挂载释放后执行删除。

个人卷扩容会预留新增容量，文件系统在下次挂载时完成扩容。网站用户名修改后，已经创建的任务继续使用创建时的容器账号与目录。

任务、个人卷、工作卷、镜像和公用目录均绑定节点。切换节点后使用该节点的镜像和数据。从开发环境提交程序时沿用源节点；工作卷容量变化时在该节点内复制。公用目录页面通过节点选择器浏览各台服务器的目录。

## 主机维护

```bash
sudo k3s kubectl -n gpu-platform get pods
sudo k3s kubectl -n gpu-platform logs deploy/platform --tail=100
sudo k3s kubectl -n gpu-workloads get pods,jobs,svc,pvc
sudo journalctl -u gpu-platform-node --since '10 minutes ago'
sudo systemctl status gpu-platform-node gpu-platform-mps lxcfs
sudo ./deploy/admin-password.sh
sudo ./deploy/upgrade-app.sh config.yaml
```

主控的 SSH 通信服务为 `gpu-platform-links`；从机的通信服务为 `gpu-platform-worker`。在相应服务器检查：

```bash
sudo journalctl -u gpu-platform-links --since '10 minutes ago'  # 主控
sudo journalctl -u gpu-platform-worker --since '10 minutes ago' # 从机
sudo systemctl status reverse-ssh                            # 从机已有隧道
```

维护一台服务器时先在“管理 → 节点”开启维护模式，待任务结束后升级驱动或执行 `git pull --ff-only` 和 `sudo ./deploy/upgrade-app.sh config.yaml`。从机断线期间，面板保留任务与端口，本地进程执行开发环境到期停止；隧道恢复后继续同步。

GPU 服务异常后，后台结束受影响任务，等待旧容器与 cgroup 释放，回收租约并重建 MPS 服务。任务记录和卷按保留设置保存。

环境保存完成后，节点进程清理本次生成的本机镜像引用。失败的清理记录位于 `/var/lib/gpu-platform/image-cleanup/`，每轮同步重试。

LXCFS 修补包位于 `deploy/packages/`，安装器校验 SHA256 后安装。升级 LXCFS 前停止用户容器。源码、补丁与重建方法见 [LXCFS 包说明](../deploy/packages/README.md)。

容器内 `htop` 和 `free` 通过 LXCFS 显示申请额度；实际 CPU 用量可从进程 CPU% 或 `/sys/fs/cgroup/cpu.stat` 读取。内层 Docker 使用独立 proc 视图，CPU 与内存总额度由外层任务执行。

重建共享默认镜像：

```bash
sudo k3s kubectl -n gpu-platform exec deploy/platform -- python manage.py bootstrap_workspace --node 节点ID --rebuild
```

在主控执行，从“管理 → 节点”取得节点 ID。首次接入从机时自动创建基础镜像构建；失败后查看构建日志，再执行上述命令重试。
