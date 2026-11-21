# 部署和使用 GPU Platform

[首页](../../README.zh-CN.md) · [English](../en/operations.md)

从一台服务器开始，后续使用同一份集群配置添加执行节点。平台服务由 Docker Compose 管理，安装器负责准备所选模块需要的主机依赖。

## 安装

支持 Ubuntu 22.04、24.04，AMD64 和 ARM64。安装前挂载好数据盘，并安装加速卡驱动。

在仓库根目录复制配置，准备安装工具：

```bash
cp config/platform.default.yaml cluster.yaml
sudo ./packages/installer/bootstrap.sh
```

继续前调整以下配置：

| 设置 | 填写内容 |
| --- | --- |
| `cluster.public_host` | 用户访问平台的地址 |
| `cluster.control_image`、`cluster.node_image` | 已在本机构建或已发布到仓库的镜像标签 |
| `hosts.main.architecture` | `amd64` 或 `arm64` |
| `defaults.slots.storage.options.pools` | 已挂载的数据盘路径 |
| `defaults.slots.metrics.options.pools` | 需要磁盘监控时填写相同存储池 |
| `defaults.parameters.datasets` | 公共数据集目录 |
| `hosts.<name>.slots` | 每台机器的模块选择；纯 CPU 主机设置 `gpu: null` |

默认配置提供了镜像标签示例。在对应架构的机器上构建本地镜像，并将标签填入 `cluster.yaml`：

```bash
docker build -f docker/control/Dockerfile -t gpu-platform-control:0.4.0 .
docker build -f docker/node/Dockerfile -t gpu-platform-node:0.4.0 .
```

构建需要已有 Docker，也可以在其他机器构建并发布。部署主机缺少 Docker 和 Compose 时，安装器会准备它们。仓库 CI 同样支持发布 AMD64 与 ARM64 镜像。

校验配置、查看计划，然后安装：

```bash
sudo platformctl validate --config cluster.yaml --host main
sudo platformctl plan --config cluster.yaml --host main
sudo platformctl install --config cluster.yaml --host main
```

安装器检查模块、准备主机设施、初始化数据库并启动服务。默认部署目录为 `/var/lib/gpu-platform/deploy/main`，可通过 `--output PATH` 修改。打开 `http://YOUR_HOST:8000`，在生成的、仅 root 可读的 `.env` 中查看 `PLATFORM_INITIAL_ADMIN_PASSWORD`，以 `admin` 登录并修改密码。节点的构建与仓库服务就绪后，会自动构建标准开发镜像。

使用外部 HTTPS 地址时，在 Web 服务前部署反向代理，并将主机 `parameters.https` 设为 `true`、`parameters.public_origin` 设为该来源。`parameters.web_port` 可修改默认端口 `8000`。

## 用 Compose 管理

```bash
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml ps
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml logs --tail 100
sudo docker compose -f /var/lib/gpu-platform/deploy/main/compose.yaml up -d
```

控制中心默认服务为 `web`、`loop`、`links`。执行主机还运行 `node` 和所选模块的服务。主机挂载的状态目录和数据盘在容器重建后继续使用。版本与配置变更通过 `platformctl upgrade` 应用。

## 添加节点或更换模块

集群文件包含四段：`cluster` 管理公共地址和镜像，`defaults` 设置默认模块与功能，`control` 配置认证和策略，`hosts` 描述机器角色与覆盖项。映射逐项合并，列表整体替换，`null` 关闭插槽。运行与存储模块的命名空间需保持一致。

例如，在现有配置中增加这台主机：

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

示例使用控制主机上已有的反向 SSH 监听端口。在指定目录准备节点独立密钥与核对过的 `known_hosts`。远程 SSH 账号需要访问节点套接字，默认组为 GID `10001`，可通过 `parameters.socket_group` 调整。在目标机器上使用同一份配置安装 `compute-a`，并按下方维护流程将更新后的配置应用到控制主机。

在支持的 ARM64 昇腾 310P 主机上，可以把 `gpu` 改为 `module: ascend`，开发镜像需包含匹配的 CANN 环境。[模块指南](modules.md)列出了全部选择与自定义扩展方法。

## 开始使用

在面板上传 SSH 公钥，选择节点与镜像，申请资源和时间范围，再使用任务页提供的 SSH 或 VS Code 配置连接。启用的公钥用于所有节点上属于你的环境，修改后也会同步到运行中的环境。

| 需要做什么 | 使用方式 |
| --- | --- |
| 交互开发 | 申请有截止时间的开发环境，需要时申请延期 |
| 执行程序 | 提交批处理任务，查看日志与退出码 |
| 保存个人文件 | `/home/<user>`，各节点有独立个人卷 |
| 保存工作数据与 Docker 状态 | `/workspace`，Docker 数据位于 `/workspace/.docker` |
| 读取共享数据 | 只读挂载的 `/datasets` |
| 复用配好的环境 | 保存为镜像，或作为批处理任务的源环境 |

标准环境提供 Rootless Docker。在 NVIDIA 节点上，可以通过 `docker run --rm --gpus all IMAGE COMMAND` 在任务分配范围内运行内部 GPU 容器。工作卷与镜像分别执行保留策略，环境保存捕获可写镜像层。

同步页面展示镜像与公共目录复制进度。成员同步自己的私有镜像，管理员还可同步共享镜像和公共目录。镜像复制要求 CPU 架构相同，私有副本占用所有者镜像额度。公共数据支持手动和周期同步、带宽限制、校验与文件续传。个人卷和工作卷留在所属节点。

## 升级、备份和卸载

将所有执行节点设为维护模式，等待活动和排队任务结束，或停止它们。在控制主机执行：

```bash
sudo platformctl upgrade --config cluster.yaml --host main
sudo platformctl maintenance-report --config cluster.yaml --host main \
  --output /tmp/platform-maintenance.json
```

通过管理连接把受保护的报告复制到执行节点，在执行节点运行：

```bash
sudo platformctl upgrade --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

报告需覆盖全部配置节点、匹配集群配置，且不超过 60 秒。每个执行节点使用新导出的报告；镜像下载较久时重新导出，用后删除副本。就绪检查未通过会在服务停止前中止操作。

一起备份数据库、`.env`、节点密钥、仓库状态、分配记录和数据卷。卸载使用同样的维护检查，将上面命令中的 `upgrade` 换为 `uninstall`。卸载停止 Compose、移除模块创建的组件，并保留用户数据和已有资源。新数据库直接创建当前结构。

<details>
<summary>其他部署设置</summary>

- **仓库网络：** Zot 默认为 `https://127.0.0.1:5000`。BuildKit 通过内部 `registry:<port>` 端点使用相同凭据与 CA。使用 DNS 地址时，以仓库 `publish_address` 指定接口；自定义构建网络使用 `build_mirrors`。外部仓库 CA 填入运行模块的 `registry_trust`，相对路径按状态目录解析。
- **默认环境配方：** `parameters.bootstrap_recipe` 选择控制主机可读的 Dockerfile，`parameters.bootstrap_context` 选择 `trusted:environment` 等构建上下文。默认合并主机自动发布内置配方；专用控制主机需要准备可读配方。控制中心管理命令 `bootstrap_workspace` 可主动提交构建。
- **配额：** 用户组 `resources` 使用模块资源键。`home_size_gib` 设置新成员默认值，成员保留自己的设置。工作卷按每个容器申请的容量校验。NVIDIA 显存申请使用 GiB，步长为 1 MiB。选择「最大」时，平台在提交时按设备预算和配额确定申请量，排队期间沿用该数值。
- **离线主机：** 通过 `parameters.image_archives` 提供镜像归档，启用 `parameters.offline`，并准备系统软件包和运行模块的版本化下载缓存。
- **防火墙：** `parameters.network` 设置公网接口与私有端口，安装器管理的防火墙服务会在主机重启后恢复规则。

公共数据的周期同步配置在 `control.sync`：

```yaml
control:
  sync:
    datasets:
      - source: main
        targets: [compute-a]
        paths: [reference]
        interval_seconds: 3600
        bandwidth_bytes_per_second: 10485760
    images: []
```

公共目录策略将源目录的修改与删除应用到指定目标子目录。镜像策略填写 `image`、`source`、`targets` 和可选带宽限制。

</details>
