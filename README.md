# 计算资源平台

为团队提供 GPU 开发环境、程序运行、镜像构建和资源管理。网页与 API 共用账号、用户组配额和审批流程。

- 开发环境按日期时段运行，支持 SSH、VS Code Remote-SSH 和容器内 Docker。
- GPU 逐卡设置算力与显存，支持共享、多卡和内层 Docker 使用 GPU。
- 程序可从镜像或运行中的开发环境启动，工作卷支持扩容和缩容。
- 管理员可审批申请、进入容器、管理账号和用户组、回收镜像与数据卷。

## 部署

主机使用 Ubuntu 22.04 ARM64、NVIDIA R620 驱动和已挂载的数据盘。参考驱动版本为 620.70.06。驱动安装后重启，再执行：

```bash
git clone https://github.com/Kndy666/gpu-platform.git
cd gpu-platform
cp config.example.yaml config.yaml
```

编辑 `config.yaml`：

| 配置 | 用途 |
| --- | --- |
| `platform.public_host` | SSH 访问的公网 IP 或域名 |
| `platform.https_origin` | 完整网页地址，HTTP 部署填写 `http://公网地址` |
| `platform.ssh_ports` | 容器 SSH 端口范围，默认 30022–30121 |
| `network.https` | 是否启用 HTTPS，默认 `false` |
| `network.public_ports_approved` | 确认开放网页和 SSH 端口后设为 `true` |
| `storage.pools` | 数据盘上的卷目录和存储类 |
| `storage.datasets` | 公用数据目录 |

默认存储池位于 `/data1`、`/data2`、`/data3`，按实际磁盘数量调整。然后部署：

```bash
sudo ./deploy/bootstrap-host.sh config.yaml
sudo ./deploy/admin-password.sh
```

安装脚本构建应用镜像，部署 K3s、Kueue、存储和镜像仓库，配置 GPU 运行时与 LXCFS，构建默认镜像 Ubuntu 22.04。主机依赖和镜像下载需要联网。日常升级使用：

```bash
sudo ./deploy/upgrade-app.sh config.yaml
```

## 本地开发

使用 Python 3.10 或更新版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
export DJANGO_DEBUG=1 PLATFORM_HTTPS=0
.venv/bin/python backend/manage.py migrate --run-syncdb
.venv/bin/python backend/manage.py bootstrap
.venv/bin/python backend/manage.py createsuperuser
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

运行测试与构建应用镜像：

```bash
DJANGO_DEBUG=1 PLATFORM_HTTPS=0 .venv/bin/python backend/manage.py test tests --noinput
docker build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12.12-slim-bookworm -t gpu-platform:0.2.0 .
```

## 文档

- [系统架构](docs/architecture.md)
- [使用与维护](docs/operations.md)
- [API 与 Agent](docs/api.md)
- [权限与运行配置](docs/security.md)
