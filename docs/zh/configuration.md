# 配置参考

[返回首页](../../README.zh-CN.md) · [English](../en/configuration.md) · [部署与运维](operations.md)

一份 YAML 文件（`cluster.yaml`）通过 `cluster`、`defaults`、`control`、`hosts` 四个顶层区块描述整个集群。`defaults` 中的映射会合并到每台主机，列表会整体覆盖继承项，将某个插槽设为 `null` 即在该主机上禁用。

## 集群镜像与网络

| 配置项 | 作用 |
| --- | --- |
| `cluster.public_host` | 用户访问控制台和 SSH 连接使用的主机名或 IP |
| `cluster.control_image` | 控制服务镜像（运行 API、调度循环、链路与通知进程） |
| `cluster.panel_image` | 独立 Vue 3 + Nginx 前端面板镜像 |
| `cluster.node_image` | 执行节点守护进程镜像 |
| `cluster.ssh_ports` | 分配给开发环境 SSH 的 TCP 端口区间 `[start, end]` |
| `cluster.state_directory` | 主机上的平台状态目录，存放数据库、密钥、Compose 文件与运行记录 |

### HTTPS 与访问端口

通过 `hosts.<name>.parameters.web_port` 修改 `panel` 容器对外发布的端口（默认 `8000`）。如果在 `panel` 前方放置外部 TLS 反向代理：

```yaml
hosts:
  main:
    parameters:
      web_port: 8000
      https: true
      public_origin: https://gpu.example.edu
```

外部反向代理需将 `Host`、`X-Forwarded-For` 与 `X-Forwarded-Proto: https` 转发给 `panel` 容器。`panel` 容器会通过 Compose 内部网络将 `/api/` 与 `/healthz` 代理至内部 `web` 服务。

## 存储池与镜像池

- **存储池**：在 `defaults.slots.storage.options.pools`（或单机覆盖项）中配置已挂载的数据盘目录，用于存放用户个人卷与工作卷。将相同目录写入 `slots.metrics.options.pools` 即可在**实时负载**中监控磁盘占用。
- **镜像池**：在 `hosts.<node>.slots.registry.options.pools` 中指定本地镜像池名称和独立挂载点上的绝对路径。
  - 默认 Zot 路由使用 `existing_images_pool`（默认为首个池）下的 `existing` 目录。如需迁移已有仓库数据，请在维护窗口停止仓库后准备好该目录，保留目录结构、属主和硬链接。
  - 新镜像会自动选择剩余空间充足且该用户镜像占比最低的镜像池。已分配镜像的池名称与挂载点请保持稳定。

## 镜像仓库网络与默认环境模板

- **镜像仓库网络**：Zot 默认监听 `https://127.0.0.1:5000`，BuildKit 使用内部 `registry:<port>` 镜像源与同一套凭据和 CA。如需自定义监听地址请设置 `publish_address`；自定义构建网络使用 `build_mirrors`；外部仓库 CA 配置在运行时 `registry_trust`（相对路径基于 `cluster.state_directory` 解析）。
- **默认环境模板**：`parameters.bootstrap_recipe` 指定控制服务可读的 Dockerfile 路径，`parameters.bootstrap_context` 指定构建上下文（如 `trusted:environment`）。控制与节点合布的主机会自动发布内置模板；独立控制主机需提供可读路径。可运行 `python -m gpu_platform_control.manage bootstrap_workspace` 手动触发构建。

## 缓存上限与自动清理

- **BuildKit 缓存**：默认目标上限 12 GiB、未用条目保留 4 小时，可通过 `slots.build.options.cache_budget_gib` 和 `cache_max_age_hours` 按主机调整。每次构建结束及空闲每 15 分钟自动清理。
- **K3s 镜像缓存**：默认清理 4 小时未使用的镜像（`slots.runtime.options.image_cache_max_age_hours`）。Kubelet 在磁盘占用达 85% 时启动空间回收，目标降至 80%。
- **宿主机 Docker 构建缓存**：安装器配置的定时任务每 15 分钟清理超过 4 小时未使用的缓存，上限 4 GiB。
- **临时中转包**：构建与导入使用的临时文件会在入库完成或彻底失败后清理，孤立临时文件在 4 小时后可回收。

## 配额与系统盘额度

- **系统盘额度**：在用户组编辑框或 `control.groups.<group>.resources.system_storage` 设置每位用户的临时存储额度（默认跨全部服务器共 `5` GiB）。任务按整数 GiB 申请，映射为 K3s Pod 的 ephemeral-storage 请求与上限（包含容器可写层和日志），任务清理后自动释放。
- **用户组与成员配额**：在 `control.groups.<group>.resources` 中按模块资源键配置上限。`home_size_gib` 作为新成员的默认个人卷上限，已有成员保留各自独立配置。工作卷配额按单个容器计算；NVIDIA 显存支持按 1 MiB 步长以 GiB 申请；申请值填 `"max"` 时会在提交阶段按剩余配额与物理上限换算固定数值。

## 历史性能记录归档

控制服务将压缩后的性能历史保存在 `<state_directory>/metrics-history/`：

- 1 分钟采样保留 7 天
- 15 分钟聚合保留 90 天
- 1 小时聚合保留 365 天

默认归档上限为 512 MiB，并预留 2048 MiB 可用磁盘空间，可在 `cluster.yaml` 中调整：

```yaml
control:
  metrics_history:
    max_mib: 512
    reserve_mib: 2048
```

当可用磁盘空间低于预留值时自动暂停写入，空间恢复后自动继续。

## 定时数据同步

在 `control.sync` 下配置共享数据集或镜像的定时同步策略：

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

共享数据集同步会将源目录的增删改同步至目标子树；镜像同步策略需指定 `image`、`source`、`targets` 及可选限速。

## 离线部署、套接字权限与防火墙

- **离线主机**：通过 `parameters.image_archives` 提供镜像归档、开启 `parameters.offline: true`，并提前准备系统软件包与运行时版本缓存。
- **节点套接字属组**：`parameters.socket_group` 设置允许访问节点守护进程 Unix 套接字的 GID（默认 `10001`）。
- **环境间 SSH**：在受支持节点（`local` 与 `reverse-ssh` 通信模块）默认开启，可通过 `features.workspace_ssh: false` 按主机关闭。
- **防火墙**：`parameters.network` 定义公网接口与私有端口，安装器托管的防火墙服务会在主机重启后自动恢复规则。
