# 计算资源平台

为团队提供 GPU 开发环境、程序运行、镜像构建和资源管理。网页与 API 共用账号、用户组配额和审批流程。

- 开发环境按日期时段运行，支持 SSH、VS Code Remote-SSH 和容器内 Docker。
- GPU 逐卡设置算力与显存，支持共享、多卡和内层 Docker 使用 GPU。
- 程序可从镜像或运行中的开发环境启动，工作卷支持扩容和缩容。
- 管理员可审批申请、进入容器、管理账号和用户组、回收镜像与数据卷。
- 一台主控提供面板，多台无面板从机通过已有 SSH 隧道接入；任务、镜像和存储绑定执行节点。

## 部署

支持 Ubuntu 22.04 / 24.04、ARM64 / AMD64，每台服务器使用独立的单节点 K3s。先安装 NVIDIA R620 驱动（至少 620.70.06）并重启，数据盘须已挂载。

主控执行：

```bash
git clone https://github.com/Kndy666/gpu-platform.git
cd gpu-platform
cp config.example.yaml config.yaml
```

编辑 `config.yaml`：

| 配置 | 用途 |
| --- | --- |
| `platform.role` | `controller` 提供面板，`worker` 只提供执行组件 |
| `platform.public_host` | SSH 访问的公网 IP 或域名 |
| `platform.https_origin` | 完整网页地址，HTTP 部署填写 `http://公网地址` |
| `platform.ssh_ports` | 容器 SSH 端口范围，默认 30022–30121 |
| `network.https` | 是否启用 HTTPS，默认 `false` |
| `network.public_ports_approved` | 确认开放网页和 SSH 端口后设为 `true` |
| `storage.pools` | 数据盘上的卷目录和存储类 |
| `storage.datasets` | 公用数据目录 |
| `controller.key_directory` | 主控专用 SSH 私钥与 `known_hosts` 目录，只挂载通信 socket 给面板 |
| `nodes` | 首次登记从机的名称、反向 SSH 端口、账户、密钥文件名和 CPU 架构 |

默认存储池位于 `/data1`、`/data2`、`/data3`，按实际磁盘数量调整。然后部署：

```bash
sudo ./deploy/bootstrap-host.sh config.yaml
sudo ./deploy/admin-password.sh
```

安装脚本构建应用镜像，部署 K3s、Kueue、存储和镜像仓库，配置 GPU 运行时与 LXCFS，构建当前节点的默认镜像 Ubuntu 22.04。主机依赖和镜像下载需要联网。

## 接入从机

保留从机现有反向 SSH 服务，例如主控 `127.0.0.1:2201` 转到从机 SSH。每台从机克隆同一项目后执行：

```bash
cp config.worker.example.yaml config.yaml
# 按实际挂载点修改 storage.pools、storage.datasets，以及 registry_budget_gib
sudo ./deploy/bootstrap-host.sh config.yaml
```

从机运行执行组件和通信服务。安装器创建 `gpu-platform` 服务账户和 `gpu-platform-worker.service`。已有反向 SSH 服务继续使用原账户。

在主控为每台从机生成不同的 Ed25519 密钥，将公钥加入从机 `/var/lib/gpu-platform/ssh-user/.ssh/authorized_keys`，目录权限 700、文件权限 600，归属 `gpu-platform`。建议公钥前加 `restrict,port-forwarding,permitopen="127.0.0.1:*"`。私钥与已核对的从机主机公钥保存在 `controller.key_directory`，私钥权限 600；`known_hosts` 的主机名使用 `[127.0.0.1]:2201` 等实际入口。密码不写入配置。

主控配置示例：

```yaml
controller:
  key_directory: /var/lib/gpu-platform/ssh-keys
nodes:
  - name: worker-3080-a
    ssh_port: 2201
    ssh_user: gpu-platform
    identity: worker-2201
    architecture: amd64
```

`identity` 是私钥文件名。其余从机分别登记其他端口与密钥。主控安装或升级后读取这些条目；后续在“管理 → 节点”维护连接、启用状态和维护模式。连接成功后自动读取从机资源、存储池并构建从机架构的默认镜像。

主控为所有节点统一分配容器 SSH 端口，用户仍连接主控公网地址，主控经 SSH 转发至实际执行节点。从机无需向公网开放网页或任务端口。每个节点保留自己的个人卷、工作卷、镜像仓库和公用目录，跨节点使用前需自行复制文件或重新构建镜像。

## 升级

在主控和各从机分别拉取代码并运行：

```bash
git pull --ff-only
sudo ./deploy/upgrade-app.sh config.yaml
```

主控会重新构建应用镜像。从机仅更新执行组件与通信服务。升级 LXCFS 前先停止该节点用户容器。暂停新任务使用节点维护模式；停用状态同步会保留已有任务和端口记录。

## 本地开发

使用 Python 3.10 或更新版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
export DJANGO_DEBUG=1 PLATFORM_HTTPS=0
.venv/bin/python backend/manage.py initdb
.venv/bin/python backend/manage.py bootstrap
.venv/bin/python backend/manage.py createsuperuser
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

运行测试与构建应用镜像：

```bash
.venv/bin/python backend/manage.py test tests --noinput
docker build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12.12-slim-bookworm -t gpu-platform:0.3.0 .
```

常规测试自动使用 `tests/settings.py`、固定测试数据和临时数据库，退出后清理。部署环境中的配置文件、数据库路径、用户组默认值和 HTTPS 设置由测试配置覆盖。需要自定义测试设置时可传入 `--settings`。

端到端测试按需设置 `PLATFORM_TEST_FEATURES`，多个能力用逗号分隔。脚本会输出跳过原因。

| 能力 | 对应测试 |
| --- | --- |
| `browser` | `visual_ui.py`，需要已有 Playwright 和 Chromium，可用 `CHROMIUM_PATH` 指定浏览器 |
| `docker` | `container-smoke.sh`、`runner-smoke.sh`，分别用 `PLATFORM_TEST_IMAGE`、`WORKSPACE_TEST_IMAGE` 指定本地镜像 |
| `cluster` | 工作空间生命周期、存储配额、镜像构建与权限、管理员 SSH、资源视图 |
| `cluster,gpu` | `gpu_sharing.py`、`workflow_lifecycle.py`，使用节点的显卡信息检查配额 |
| `cluster,gpu,docker` | `docker_workspace.py`，以及 GPU 共享测试中的容器内 Docker 检查 |
| `containerd` | `node_image_cleanup.py`，在 K3s 节点检查临时镜像标签回收 |
| `registry` | `image_recycling.py`，用 `PLATFORM_TEST_REGISTRY_DIR` 指定独立 Zot 测试目录，`PLATFORM_DATA` 指向其 `data` 子目录 |
| `recovery` | 与 `cluster` 一起启用 `recovery_check.py`，会重启测试节点的 K3s、CSI 并注入进程故障 |
| `network` | 为工作空间工具、Docker 验收启用软件源联网检查 |
| `nccl` | 为 GPU 共享验收启用 NCCL，`PLATFORM_NCCL_TEST_DIR` 提供 `check.c` 和运行库，可通过 `PLATFORM_CUDART_LIBRARY` 指定 CUDA 运行库文件名 |

集群测试从 `PLATFORM_TEST_CONFIG` 读取目标部署配置，默认使用 `PLATFORM_CONFIG` 或项目的 `config.yaml`。`PLATFORM_TEST_KUBECTL` 可指定命令，例如 `kubectl --context staging`。实际显卡数量不足的场景会跳过。HTTP 入口可用 `PLATFORM_TEST_BASE` 覆盖，带 `--base` 的脚本使用该参数。通过网页登录的验收脚本支持 `PLATFORM_TEST_ADMIN_USER` 和 `PLATFORM_TEST_ADMIN_PASSWORD`，默认读取初始化管理员凭据。

```bash
PLATFORM_TEST_FEATURES=browser .venv/bin/python tests/e2e/visual_ui.py /tmp/platform-ui
PLATFORM_TEST_FEATURES=cluster,gpu PLATFORM_TEST_CONFIG=/path/to/test-config.yaml \
  .venv/bin/python tests/e2e/gpu_sharing.py
```

## 文档

- [系统架构](docs/architecture.md)
- [使用与维护](docs/operations.md)
- [API 与 Agent](docs/api.md)
- [权限与运行配置](docs/security.md)
