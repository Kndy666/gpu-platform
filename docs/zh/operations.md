# 部署与运维

[返回首页](../../README.zh-CN.md) · [English](../en/operations.md) · [配置参考](configuration.md)

从单台服务器起步，团队扩展后再用同一份集群配置文件加入更多执行服务器。平台服务通过 Docker Compose 运行，安装器自动准备所选模块需要的主机依赖。

## 安装平台

准备 Ubuntu 22.04 或 24.04 系统（支持 AMD64 和 ARM64），提前挂载数据盘并安装加速卡驱动。

在仓库根目录复制默认配置并安装 `platformctl`：

```bash
cp config/platform.default.yaml cluster.yaml
sudo ./packages/installer/bootstrap.sh
```

继续之前先在 `cluster.yaml` 中核对以下配置项：

| 配置项 | 填写说明 |
| --- | --- |
| `cluster.public_host` | 用户在浏览器访问和 SSH 连接使用的主机名或 IP |
| `cluster.control_image`、`cluster.panel_image`、`cluster.node_image` | 本地构建或镜像仓库中可用的镜像标签 |
| `hosts.main.architecture` | `amd64` 或 `arm64` |
| `defaults.slots.storage.options.pools` | 已挂载的数据盘目录 |
| `defaults.slots.metrics.options.pools` | 需要监控磁盘空间时填写相同目录 |
| `defaults.parameters.datasets` | 共享数据集目录 |
| `hosts.<name>.slots` | 每台主机的模块配置；纯 CPU 服务器将 `gpu` 设为 `null` |

如需在目标架构本机构建镜像，可在对应架构主机上执行并在 `cluster.yaml` 中使用相同标签：

```bash
docker build -f docker/control/Dockerfile -t gpu-platform-control:0.4.0 .
docker build -f docker/panel/Dockerfile -t gpu-platform-panel:0.4.0 .
docker build -f docker/node/Dockerfile -t gpu-platform-node:0.4.0 .
```

检查配置、预览安装计划并开始安装：

```bash
sudo platformctl validate --config cluster.yaml --host main
sudo platformctl plan --config cluster.yaml --host main
sudo platformctl install --config cluster.yaml --host main
```

安装器会校验模块兼容性、准备主机基础环境、初始化数据库并启动 Docker Compose 服务。默认部署目录为 `/var/lib/gpu-platform/deploy/main`（可通过 `--output PATH` 修改）。

打开 `http://YOUR_HOST:8000`，查看生成的仅 root 可读的 `.env` 中的 `PLATFORM_INITIAL_ADMIN_PASSWORD`，以 `admin` 登录并立即修改密码。构建与仓库服务就绪后，标准开发环境镜像会自动开始构建。

如需配置 HTTPS，可在 `panel` 前方放置外部 TLS 反向代理，并在控制主机上配置 `parameters.https: true` 与 `parameters.public_origin`。反向代理、存储池、缓存与定时同步详见[配置参考](configuration.md)。

## 使用 Compose 日常维护

```bash
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml ps
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml logs --tail 100
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml up -d
```

控制主机运行 `panel`（Nginx 托管 Vue 面板并将 `/api/` 与 `/healthz` 反向代理至内部 `web`）、`web`（内部 API 服务）、`loop`、`links` 以及启用通知时的 `notifier`。执行主机运行 `node` 及 `registry`、`builder` 等模块服务。数据库、密钥与用户数据保存在宿主机挂载目录，容器重建后保持完好。

## 添加新服务器

在 `cluster.yaml` 中追加新服务器条目：

```yaml
hosts:
  compute-a:
    roles: [node]
    architecture: arm64
    address: 127.0.0.1
    slots:
      gpu: null
      transport:
        module: reverse-ssh
        options:
          host: 127.0.0.1
          ssh_port: 2201
          ssh_user: gpu-platform
          identity: compute-a
          key_directory: /var/lib/gpu-platform/ssh-keys
      entry:
        module: controller-forward
        options:
          public_host: compute.example.edu
```

在 `key_directory` 中提前准备节点独立密钥与校验过的 `known_hosts`，并让远程 SSH 账号具备访问节点套接字的属组权限（默认 GID `10001`）。用同一份 `cluster.yaml` 在新机器安装 `compute-a`，随后按下方升级步骤在控制主机应用变更。

在 ARM64 昇腾 310P 服务器上，将 `slots.gpu.module` 设为 `ascend`，并准备匹配版本的 CANN 基础镜像。全部插槽选项见[模块指南](modules.md)。

## 监控与用量统计

- **后台管理 → 实时负载**：查看各服务器 CPU、内存、GPU/NPU、磁盘和网络实时使用率，以及压缩保存的历史记录（1 分钟采样保留 7 天，15 分钟聚合保留 90 天，1 小时聚合保留 365 天）。
- **后台管理 → 用量统计**：查看当前算力分配比例以及按日期、成员、服务器和设备型号筛选的历史 GPU/NPU 机时，支持一键复制文本汇总。
- **后台管理 → 存储与卷**：查看各服务器存储池可用容量和用户个人卷/工作卷，可清理空闲卷。

## 升级、备份与卸载

先将所有执行服务器切换为维护模式，等待运行中和排队的任务结束或停止。在控制主机执行：

```bash
sudo platformctl upgrade --config cluster.yaml --host main
sudo platformctl maintenance-report --config cluster.yaml --host main \
  --output /tmp/platform-maintenance.json
```

通过管理通道将维护报告复制到执行服务器后执行：

```bash
sudo platformctl upgrade --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

维护报告必须覆盖全部已配置服务器、与当前集群配置一致且生成时间在 60 秒以内。每台执行服务器应使用最新导出的报告，完成后删除副本。就绪检查失败会在停止服务前中止操作。

备份时需一并保存数据库、`.env`、节点密钥、`metrics-history/`、镜像仓库状态、加速卡分配记录与数据卷。卸载流程与升级一致，将 `upgrade` 替换为 `uninstall` 即可；卸载会停止 Compose 并清理模块托管的主机配置，保留用户数据与已有基础设施。
