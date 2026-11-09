# 计算资源平台

一个 Django 应用镜像，包含学生门户、管理员审批、SQLite WAL、后台同步和回收。计算排队交给 Kueue，本机工作进程管理 GPU 份额，卷容量交给 OpenEBS Rawfile。

架构与功能见 [架构说明](docs/architecture.md)，日常使用和维护见 [使用与管理](docs/operations.md)。

网页的「API 访问」可生成令牌、查看 OpenAPI 和下载 Agent skill。接口覆盖资源申请、运行程序、SSH、镜像、数据卷、公用目录及管理操作，使用方式见 [API 与 Agent](docs/api.md)。

开发运行（Python 3.10+）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
export DJANGO_DEBUG=1 PLATFORM_HTTPS=0
.venv/bin/python backend/manage.py migrate --run-syncdb
.venv/bin/python backend/manage.py bootstrap
.venv/bin/python backend/manage.py createsuperuser
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

```bash
DJANGO_DEBUG=1 .venv/bin/python backend/manage.py test tests --noinput
docker build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12.12-slim-bookworm -t gpu-platform:0.2.0 .
```

重装部署：Ubuntu 22.04 ARM64，先安装 NVIDIA R620 驱动（已验证 620.70.06）并重启，将数据盘挂载至 `/data1`、`/data2`、`/data3`。按实际磁盘数量修改存储池；安装入口校验数据盘挂载，不格式化磁盘。

```bash
git clone https://github.com/kndy666/gpu-platform.git
cd gpu-platform
cp config.example.yaml config.yaml
# 编辑 config.yaml：public_host、https_origin、storage.pools、storage.datasets
# HTTP 部署将 https_origin 写为 http://公网地址，network.https 保持 false
# 确认 HTTP 80 与 SSH 30022–30121 后，设 network.public_ports_approved: true
sudo ./deploy/bootstrap-host.sh config.yaml
```

GPU 共享需要 NVIDIA R620 驱动、MPS v3 的服务端 namespace/SM 绑定，以及 Container Toolkit 1.20.1 或更新版本。当前服务器使用 R620.70.06、Linux 5.15；驱动原生 cgroup 显存限制使用 misc 后端。安装器配置 MPS 服务、OCI 限额钩子和工作容器运行时。应用采用单实例 `Recreate` 更新，永久卷独立管理，默认卸载保留数据。

入口安装主机依赖、从源码构建应用镜像，初始化 K3s、存储、Kueue 和 GPU 运行时，安装仓库中带校验的 LXCFS 修补包，随后自动构建唯一默认镜像 Ubuntu 22.04。无需旧服务器的业务镜像归档、数据库或 `dist` 目录。主机依赖安装和镜像下载需要联网。

新建环境页显示 GPU、CPU、内存与存储余量。任何运行中的开发环境都可保存镜像或作为程序来源；工作卷容量不变时复用原卷，容量变化时离线复制到新卷，校验完成后回收原卷。缩容目标需容纳已有数据。

常规应用升级使用 `sudo ./deploy/upgrade-app.sh config.yaml`。初始管理员密码通过 `sudo ./deploy/admin-password.sh` 读取，登录后可修改。

- [架构](docs/architecture.md)
- [运行、升级和回滚](docs/operations.md)
- [安全边界](docs/security.md)
- [API 与 Agent](docs/api.md)
- [网页与 API 对照](docs/api-parity.md)
- [验证与自检](docs/acceptance.md)
