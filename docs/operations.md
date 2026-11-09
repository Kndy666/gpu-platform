# 使用与管理

网页入口由 `config.yaml` 的 `platform.https_origin` 配置。日期和时段按北京时间显示。管理员与普通用户使用同一登录入口。

## 管理员

1. 点击侧栏“管理”。待审核列表中打开申请，确认资源、时段及运行来源，再批准或填写原因拒绝。
2. “成员”管理账号、密码、启停和管理员权限。“增加成员”设置用户名和初始密码。用户登录后可自行修改密码。
3. 在成员编辑页选择用户组和个人卷额度。增加个人卷额度会预留新增容量并申请扩容，文件系统在下次挂载时完成扩容。“存储”查看各盘和用户卷。
4. “用户组”支持新增、编辑、删除和设置默认组。限额组的 GPU 表格分别设置总算力、总显存、单卡算力上限和单卡显存上限；算力按百分比填写，150 表示一张全卡加一张半卡的总份额。其余设置包括 CPU、内存、存储、镜像、并发任务数和环境最长时段；“资源不限额”会清空组配额，关闭后需重新填写。删除含成员的组时选择迁入组，目标组须容纳成员现有个人卷额度；系统至少保留一个组。
5. “资源分配”按日期显示实际 GPU 卡、CPU、内存及预约时段。运行实例的具体 GPU 由本机工作进程采集。普通用户只能打开自己的任务；管理员可打开全体任务。
6. 任务详情可停止任务；开发环境可选新的结束日期进行延期。审计记录保留审批、停止、保存环境等操作。

管理页面统一使用工作台布局和选项卡。成员编辑页随屏宽伸缩，在窄屏上堆叠显示账号信息、用户组与存储、账号权限。“镜像”查看回收时间、异常和保留操作；“设置”调整工作卷与运行快照保留时长；“审计”查看操作记录。

## GPU 服务恢复

GPU 共享服务重启后，节点进程会在旧容器及其 cgroup 释放后回收旧租约，重建 `shared` MPS 服务并恢复卡的可分配容量。控制进程仍在运行而 `shared` 服务已消失时，也按此流程回收。释放暂未完成的租约会保留额度，并持续更新不可用状态；后台继续重试。已因 GPU 上下文失效而结束的程序保留失败记录，数据卷按配置期限保留。

## 管理员 SSH 接入

管理员先在侧栏“SSH 公钥”添加自己设备的公钥，再进入“管理 → 容器”。列表支持按名称、成员、容器编号、状态和类型筛选；运行中的容器行提供终端图标，打开后可复制 SSH 命令或 `~/.ssh/config` 配置。以目标容器所属用户名登录，认证使用管理员自己的密钥，进入后可执行 `sudo -i`。

现有运行容器和新容器都接受管理员公钥。公钥启停、账号启停和管理员角色变化通过同步进程更新公钥文件，无需重新启动工作容器。Secret 挂载传播需要少量时间；已有 SSH 连接保持。普通用户继续只访问自己的容器。

## 公用目录

侧栏和管理页均有“公用目录”，支持逐层浏览、当前目录搜索和文件下载。对应服务器配置 `storage.datasets`，当前为 `/data1/platform/datasets`，用户容器内为 `/datasets`。应用只读挂载该目录，上传通过服务器 SSH 完成；上传后保留读取账号所需的目录和文件权限。网页不跟随符号链接。

## 用户：准备环境，再运行程序

1. 添加 SSH 公钥。
2. 新建开发环境，页面直接显示 GPU、CPU、内存与存储余量。GPU 默认 0，也可申请 GPU；填写开始和结束日期，获批后按时段启动。
3. 使用任务页 SSH 命令连接，或使用 VS Code Remote-SSH。
4. 默认环境为 Ubuntu 22.04，含 SSH、sudo、网络与编译工具、bubblewrap、htop、Bash 高亮和 Docker。可用 `sudo apt update`、`sudo apt install python3 python3-pip` 安装系统软件，也可在个人目录安装 Python、Conda 和框架。
5. 准备就绪后，在环境详情点击“运行程序”。选择 GPU 数量，在展开的表格中逐行设置算力和显存，再设置 CPU、内存，并直接填写 Shell 命令。
6. 运行程序时可选择任意本人运行中的开发环境，并编辑工作卷容量。获批后保存容器可写层，确认镜像后停止源容器。个人卷继续复用；工作卷大小不变时直接复用原卷，改变大小时先离线复制到新卷，复制成功后回收原卷，再排队运行。
7. 程序持续运行至主命令结束。日志位于 `/workspace/.platform/`，SSH 断开后主命令继续。退出 0 记录成功，其他退出码记录失败。失败不自动重试。

运行页提供两种来源：

Kubernetes 的 PVC 支持扩容，原地缩容不可用。平台在容量变化时统一新建卷并复制，保留文件内容、权限、硬链接、符号链接及稀疏文件。复制期间需要新卷所占的临时磁盘空间；目标卷放不下现有文件时申请失败，原卷继续保留，按工作卷保留策略回收。[Kubernetes 容量说明](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#expanding-persistent-volumes-claims)

| 项目 | 使用镜像 | 使用开发环境 |
| --- | --- | --- |
| 环境来源 | 已有的共享镜像或个人镜像 | 自己运行中的开发环境（可带 GPU） |
| 文件环境 | 使用选中镜像的版本 | 将当前容器可写层保存为个人镜像 |
| `/home/<网站用户名>` | 挂载本人的个人卷 | 挂载本人的个人卷 |
| `/workspace` | 新建工作卷 | 容量相同时复用源卷，变化时复制到新卷 |
| 原开发容器 | 保持当前状态 | 保存成功后停止，等待资源释放完成 |

两种来源均需管理员批准，再由队列分配资源并启动新的运行容器。使用镜像时，平台固定镜像 digest，创建工作卷和运行 Job。使用开发环境时，本机工作进程保存可写层并推送到个人仓库；平台确认保存完成后停止源容器，等待清理完成，再用保存的镜像和原数据卷创建运行 Job。保存失败时，源环境继续按原时段运行。

镜像保存文件改动，挂载的数据卷单独保留。运行进程按填写的命令重新启动；需要虚拟环境时，在命令中激活或填写解释器完整路径。开发环境详情中的“保存镜像”只创建镜像，原容器继续运行；以后通过“使用镜像”提交运行时会新建工作卷。

### 在个人目录安装 Python

下面的示例使用 uv 下载独立 Python，默认镜像无需预装 Python：

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/install-uv.sh
sh /tmp/install-uv.sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
uv venv --python 3.12 --seed $HOME/venvs/app
source $HOME/venvs/app/bin/activate
# 按项目要求安装框架、选择 ARM64 和 CUDA 对应的软件源
```

[uv 官方 Python 安装说明](https://docs.astral.sh/uv/guides/install-python/)。个人目录下的 Python 和虚拟环境会随同账号保留。运行命令可以使用完整路径，或先激活虚拟环境：

```bash
source $HOME/venvs/app/bin/activate
python $HOME/project/main.py
```

开发环境按选定的绝对时段运行，到点停止；资源不足时可能等待，因此队列等待会消耗剩余可用窗口。程序运行至主命令结束，时间轴以开放区间显示尚未结束的程序任务。未来预约不会抢停正在执行的程序。

## 镜像构建

### 在开发环境中使用 Docker

默认共享镜像“Ubuntu 22.04”包含基础工具和 Docker。平台启动独立的 Rootless Docker 引擎，可直接使用 `docker`、`docker build` 和 `docker compose`。

Docker 数据位于 `/workspace/.docker`，包含内层镜像、缓存、容器可写层和 Docker 命名卷，计入工作卷容量。工作卷默认保留 3 天；需要长期保存的内层镜像可推送到自己的外部镜像仓库，或用 `docker save` 保存到个人卷。外层“保存镜像”保存系统文件改动，挂载的工作卷独立保留。

申请 GPU 后，可在内层容器中使用：

```bash
docker run --rm --gpus all -v /workspace:/workspace -w /workspace IMAGE COMMAND
```

`all` 使用当前环境获分配的全部物理 GPU。每行可以设置不同规格，例如第一张 100% / 8 GiB、第二张 50% / 4 GiB，对应 CUDA 设备 0 分配 114 SM / 8 GiB、设备 1 分配 56 SM / 4 GiB；四卡和整卡申请使用相同入口。任务全部卡的资源满足后统一启动。内外层所有进程共同使用申请的 CPU、内存、SM 和逐卡显存额度，平台按外层任务计数。显存达到硬上限时 CUDA 返回 OOM。运行程序时使用前台命令，平台跟随命令退出码结束任务；开发环境中的后台服务随外层环境停止。

从开发环境提交程序时，新的环境重新启动引擎并执行所填命令。工作卷容量相同时复用，容量变化时复制到新卷。原来的运行进程按任务生命周期结束，镜像和卷数据继续保留。

平台提供镜像仓库证书和本人的只读登录配置，可拉取共享及本人私有镜像。平台仓库中的镜像通过网页/API 构建与保存，纳入镜像额度和回收管理；直接推送可使用自己的外部仓库。登录配置位于临时运行目录。`docker run -p` 发布到当前开发环境，可通过 SSH 本地转发访问。Docker 镜像需要提供 ARM64 版本。

Docker 引擎日志位于 `/workspace/.platform/docker.log`。内层单独的 `--cpus`、`--memory` 需要额外 cgroup 委派；当前统一由外层任务的 cgroup 执行总额度。SSH 环境中的 LXCFS 继续提供申请额度视图，内层容器新建的 proc 视图可能显示宿主机总量。

管理员可使用 `python manage.py bootstrap_workspace --rebuild` 重建共享默认镜像，统一安装器自动构建该镜像。

在“环境镜像”点击“使用此镜像”，会进入已选中该镜像的运行申请页。“复制地址”用于获取镜像地址，例如填写 Dockerfile 的 `FROM`。

构建镜像时填写名称、Dockerfile、可选 HTTPS Git 上下文。构建结果推送到本人的私有仓库，可用于后续申请。ARM64 依赖由 Dockerfile 决定，构建阶段不分配 GPU。

构建 Job 上限 1 小时、4CPU/8GiB，构建上下文和层缓存是临时卷。每用户同时一份构建。日志取最近 3000 行、250KB。构建完成 24 小时后回收 Job/Pod。

### 镜像回收

| 来源 | 默认保留策略 |
| --- | --- |
| 开发环境详情中的“保存镜像” | 长期保留 |
| Dockerfile 构建 | 长期保留 |
| 从开发环境提交运行时自动保存 | 成功或取消后 24 小时，失败后 72 小时 |

自动保存的镜像显示在“运行快照”。所有引用它的申请结束，且计算容器实际释放后，开始保留期计时。待审核、排队、启动和运行任务都会阻止回收。保留期内有新任务使用它时，取消原计时，待全部任务释放后重新计时。管理员修改的保留时长用于之后开始计时的快照。

“长期保留”将运行快照转为普通个人镜像。“回收”可安排闲置的个人镜像在 24 小时后删除，期间可点击“长期保留”撤销。共享基础镜像保持保留。

到期时先停止该镜像的新申请，再按版本标签从仓库删除；其他指向相同内容的版本继续保留。删除失败会每 5 分钟重试，管理页显示错误。数据库保留镜像和任务的历史关联，镜像额度在仓库删除成功后释放。仓库启用 1 小时延迟和 1 小时间隔的垃圾回收，磁盘空间在无引用数据层被清理后释放。

本机环境保存操作在成功或失败后清理本次创建的本机镜像引用，失败时持久化重试记录。

## 运维

```bash
sudo k3s kubectl -n gpu-platform get pods
sudo k3s kubectl -n gpu-platform logs deploy/platform --tail=100
sudo k3s kubectl -n gpu-workloads get pods,jobs,svc,pvc
sudo journalctl -u gpu-platform-node --since '10 minutes ago'
sudo ./deploy/admin-password.sh
sudo ./deploy/upgrade-app.sh config.yaml
```

`gpu-platform-node.service` 处理受限的环境保存请求，以及 GPU 库存、逐卡租约、MPS 分区创建和回收。`gpu-platform-mps.service` 提供独立的主机 MPS 服务。它们通过本机 containerd、驱动和 Kubernetes 工作；学生容器挂载本任务的私有 MPS 接入目录。

保存操作结束时会清理本次创建的两个本机镜像引用。清理失败的操作记录位于 `/var/lib/gpu-platform/image-cleanup/`，工作进程每轮重试，重启后继续处理；成功后删除记录。个人镜像仓库中的版本继续按平台回收规则保留。

工作卷默认保留 3 天，在到期前一天显示回收通知，到期后自动删除；管理员可在“管理 → 设置”修改保留天数，新值用于之后结束的任务。

“管理 → 存储”可删除空闲的个人卷与工作卷，确认页显示卷名、容量和所属成员。运行中或仍有实际挂载的卷等待资源释放；删除异常显示在卷状态中，后台自动重试。

Bash 在首次启动时从 `/etc/skel` 初始化 `.bashrc` 和 `.profile`，已有用户配置继续保留。提示符、ls、grep 使用颜色，交互输入高亮由 [ble.sh](https://github.com/akinomyoga/ble.sh/releases/tag/v0.4.0-devel3) 提供。

容器账号与网站用户名一致，例如 `admin` 登录容器后个人目录为 `/home/admin`。任务创建后保存登录名，之后修改网站用户名时，已运行任务的账号和目录继续沿用创建时的名称。账号名使用常规 Linux 登录名格式。

默认环境使用 Ubuntu 官方 APT 源，pip 和 Conda 保持各自默认源。镜像构建期间可临时使用加速源下载基础工具，构建完成后恢复官方 APT 配置。

用户容器只读挂载 LXCFS 的资源信息文件，`htop`、`free`、CPU 信息和常用自动线程数按申请额度显示。LXCFS 服务通过 systemd 开机启动，K3s 保持独立的服务生命周期。

Ubuntu 22.04 的 LXCFS 5.0.0 在 cgroup v2 下会让 `/proc/stat` 显示宿主机全部 CPU，影响 htop 3.0.5 的核数。平台使用 `5.0.0-0ubuntu2+platform2` 包，回移 [LXCFS 上游修复 #687](https://github.com/lxc/lxcfs/pull/687)。安装器校验 `dist` 中修复包的 SHA256，在用户容器全部停止时安装并启动服务。修复源码与包校验值见 `deploy/patches/`；构建脚本 `deploy/build-resource-view.sh` 在临时 Docker 容器中编译库，保留原 Ubuntu 包的程序与配置，使用当前服务安装脚本。

CPU 配额由 cgroup `cpu.max` 执行。当前内核的 cgroup v2 提供容器 CPU 总量统计；LXCFS 的 `/proc/stat` 单核时间仍沿用宿主机对应核的数据。查看程序的实际占用可使用 htop 进程列表的 CPU% 列，或采样容器 `/sys/fs/cgroup/cpu.stat` 的 `usage_usec`。

资源视图验收同时检查 `/proc/cpuinfo`、`/proc/stat`、内存额度、CPU 配额和真实终端中的 htop 仪表：

```sh
sudo python3 tests/e2e/resource_view.py <Pod名称> --cpu 12 --memory-gib 20
sudo python3 tests/e2e/workspace_tools.py --resource-view-only
```

修复包构建输入来自 [Ubuntu LXCFS 源码与软件包目录](https://ports.ubuntu.com/ubuntu-ports/pool/universe/l/lxcfs/)：`lxcfs_5.0.0.orig.tar.gz` 存为 `lxcfs-5.0.0.tar.gz`，`lxcfs_5.0.0-0ubuntu2.debian.tar.xz` 存为 `debian.tar.xz`，对应架构的 `lxcfs_5.0.0-0ubuntu2_*.deb` 存为 `lxcfs-original.deb`。将这三个输入放入同一个临时目录，执行 `bash deploy/build-resource-view.sh <临时目录>`。脚本校验源码哈希并输出修复包；正式包放入 `dist`，其文件名和 SHA256 登记在 `deploy/patches/lxcfs-packages.json`。

项目不保留迁移目录。`MIGRATION_MODULES` 关闭 portal 的迁移，首次启动通过 `migrate --run-syncdb` 按当前模型创建业务表；已有业务表继续使用。

容器运行后，任务详情的“连接环境”同时提供 SSH 命令和可粘贴到 `~/.ssh/config` 的配置，均支持一键复制。配置包含该任务的独立 Host 别名、地址、端口、用户名和 HostKeyAlias，可供 SSH 客户端与 VS Code Remote SSH 使用。
