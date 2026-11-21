# 安装与维护

支持 Ubuntu 22.04、24.04，AMD64 和 ARM64。安装前挂载数据盘；启用 NVIDIA 模块的机器先安装满足模块检查要求的驱动。K3s、Kueue、Rawfile、LXCFS、OCI 配置和模块服务由安装钩子准备。

## 准备集群配置

```bash
cp config/platform.default.yaml cluster.yaml
```

填写公网地址、每台机器的架构、存储池路径和公共目录。镜像仓库地址需能从主机及工作环境访问。仓库安装交接向运行模块提供端点和 CA 文件。外部仓库可使用运行模块的 `registry_trust` 显式配置，相对 CA 路径按集群状态目录解析。生成的仓库证书位于该目录下的 `registry/tls.crt`。Zot 默认将公布端口绑定到主机回环地址，并与 BuildKit 共用私有 Compose 桥接网络。构建交接提供内部 `registry:<port>` 镜像端点，保留原镜像引用。公布地址使用 DNS 名称时，以仓库 `publish_address` 指定主机接口；自定义构建网络可通过 `build_mirrors` 调整内部端点。证书需同时覆盖公布地址和镜像端点的主机名。

| 配置段 | 内容 |
| --- | --- |
| `cluster` | 公网地址、SSH 端口池、默认语言、控制中心与节点镜像、状态目录 |
| `defaults` | 插槽模块、模块选项、功能开关、公共参数 |
| `control` | 认证、通知、初始用户组与资源配额、同步策略、数据库 |
| `hosts` | 角色、架构、地址、插槽与功能覆盖、主机参数 |

默认文件同时打包在安装工具中。机器配置覆盖映射中的指定项，列表整体替换；插槽设为 `null` 后停用。纯 CPU 机器示例：

```yaml
hosts:
  main:
    roles: [control, node]
    architecture: amd64
    slots:
      gpu: null
```

运行模块与存储模块的命名空间保持一致。需要磁盘监控时，在监控模块中配置对应的存储池路径。用户组的 `resources` 使用模块登记的资源键，例如 `cpu`、`memory`、`work_storage`、`image_storage`、`gpu_compute`、`gpu_memory`。用户组的 `home_size_gib` 设置新增成员的默认个人卷容量，成员可单独调整；修改用户组默认值会保留已有成员的容量。每个节点上的个人卷相互独立，文件不会自动同步。个人卷按成员设置的容量和物理存储池容量分配，工作卷配额按每个容器的申请容量校验。容量使用 GiB/TiB。NVIDIA 模块按驱动报告的总显存分配，显存请求使用整数 GiB。

```bash
uv run platformctl validate --config cluster.yaml --host main
uv run platformctl plan --config cluster.yaml --host main
```

`validate --machine` 增加本机检查。普通校验和规划检查配置与模块声明。

## 构建和安装

在有 Docker 的机器构建镜像：

```bash
docker build -f docker/control/Dockerfile -t gpu-platform-control:0.4.0 .
docker build -f docker/node/Dockerfile -t gpu-platform-node:0.4.0 .
```

将 `cluster.control_image` 和 `cluster.node_image` 设为本地标签或自己的仓库标签。仓库中的 GitHub Actions 工作流构建 AMD64 和 ARM64 镜像；部署主机使用仓库标签前先发布对应版本。

```bash
sudo ./packages/installer/bootstrap.sh
sudo platformctl install --config cluster.yaml --host main
```

安装器为新主机准备固定版本 Docker Engine 29.1.3 与 Compose。已有安装需支持 Docker API 1.44 及以上，并具备 Compose 插件。默认限制的基础设施端口为 6443 和 10250。私有端口规则由 `gpu-platform-firewall.service` 管理；`public_interface: auto` 选择优先级最高的默认路由接口，服务在重启后恢复规则。安装器执行检查和模块钩子，写入解析后的配置与密钥环境文件，准备容器镜像、创建当前数据库结构、初始化默认数据，再启动 Compose。生成目录默认为 `/var/lib/gpu-platform/deploy/main`，可用 `--output PATH` 修改。

```bash
sudo docker compose --project-directory /var/lib/gpu-platform/deploy/main \
  -f /var/lib/gpu-platform/deploy/main/compose.yaml ps
sudo docker compose --project-directory /var/lib/gpu-platform/deploy/main \
  -f /var/lib/gpu-platform/deploy/main/compose.yaml logs --tail 100 web loop node
```

网页默认端口为 `8000`，由 `hosts.<name>.parameters.web_port` 配置。使用 HTTPS 时，将主机 `parameters.https` 设为 `true`，将 `parameters.public_origin` 设为公网来源，并部署前置 HTTPS 反向代理。Compose 根据这些值配置安全 Cookie 和受信任来源，额外环境变量填写在 `parameters.environment` 中。生成的 `.env` 保存 `DJANGO_SECRET_KEY` 和初始管理员密码 `PLATFORM_INITIAL_ADMIN_PASSWORD`。在本机读取仅 root 可访问的文件，以 `admin` 登录后修改密码。Django 密钥应随数据库一起备份。已有账号在初始化时保留。

默认的控制与执行合并节点将内置环境配方发布到状态目录下的 `bootstrap/environment/Dockerfile`。节点报告构建和仓库能力、且已有管理员后，控制循环会排队构建默认镜像。执行节点使用相同的已配置配方及各自的 `build.options.trusted_contexts.environment` 目录。专用控制节点或自定义运行时需在控制主机上保存可读配方。每个目标节点的 `parameters.bootstrap_recipe`（或继承的 `defaults.parameters.bootstrap_recipe`）选择这个控制端文件，可用绝对路径或相对控制端状态目录的路径；目标节点的 `parameters.bootstrap_context` 应填写该节点构建器已配置的 `trusted:<name>`。配方缺失时跳过自动初始化。也可在 Web 服务中执行 `python -m gpu_platform_control.manage bootstrap_workspace --all-nodes --recipe PATH --context trusted:environment --wait 0` 主动排队构建。

## 添加执行节点

所有机器使用同一文件，控制中心也可以执行任务。远程节点显式配置通信和登录入口：

```yaml
hosts:
  main:
    roles: [control, node]
    architecture: amd64
  compute-a:
    roles: [node]
    architecture: arm64
    address: 127.0.0.1
    slots:
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

示例使用主控上已有的反向 SSH 监听端口。为每个节点设置独立密钥和经过核对的 `known_hosts`，保存在指定密钥目录。远程 SSH 账号需要访问节点套接字，默认套接字组为 GID `10001`，可通过 `parameters.socket_group` 修改。在目标机器执行 `platformctl install --host compute-a`。

控制中心统一分配用户 SSH 端口，登录入口模块建立连接路径。公共目录、镜像仓库和卷池各自归属节点。

## 使用环境与镜像

添加 SSH 公钥，选择节点、镜像、资源和日期时段后提交申请。任务页提供 SSH 和 VS Code 配置。开发环境按获批截止时间停止，延期申请遵循账号审批策略和延期次数。

程序运行至命令退出，任务记录退出码和日志。`/workspace` 保存工作数据，`/home/<用户>` 保存个人数据，`/datasets` 只读挂载公共目录。Docker 数据保存在工作卷中。

保存环境会生成可写层镜像，个人卷和工作卷分别管理。程序可以使用镜像或现有开发环境；后者先准备环境快照及对应工作卷，再派发执行。

“同步”位于主导航。普通用户只能同步自己的私有镜像，并查看自己的同步记录；新副本归自己，计入自己的镜像额度。管理员可以查看全部记录，同步共享镜像时目标副本自动保持共享，并可触发公共目录同步。页面显示源节点、目标节点、传输字节数、状态和结束时间。周期策略配置在 `control.sync`：

公用目录通过所选节点通信模块逐文件传输，中断后从目标数据盘保存的文件偏移继续。每个文件传完后校验 SHA-256，全部文件校验成功后再提交目录变更；未变化的文件在目标节点直接复用。

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

镜像策略填写 `image`、`source`、`targets` 和可选带宽限制，源目标节点使用相同架构。公共目录策略把源目录的修改和删除同步到指定目标子目录。

## 升级和卸载

在节点管理页把所有执行节点设为维护模式，等待活动及排队任务结束，或停止这些任务。升级校验当前报告、准备镜像，再次检查维护状态后停止服务。主机配置、模块版本或镜像选择变化时，重新执行主机和模块安装钩子，再检查当前数据库结构并启动 Compose。

在控制主机执行：

```bash
sudo platformctl upgrade --config cluster.yaml --host main
sudo platformctl maintenance-report --config cluster.yaml --host main \
  --output /tmp/platform-maintenance.json
```

通过现有管理连接把受保护的报告复制到执行节点，在该节点执行：

```bash
sudo platformctl upgrade --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

报告必须匹配完整集群配置，包含每个已配置节点，并满足协议版本兼容、报告时间不超过 60 秒。执行节点还会在停止服务前即时查询自己的本地守护进程。每个节点使用新导出的报告；镜像下载超过有效期时重新导出。报告缺失、过期、不匹配、仍有活动任务或未进入维护状态，都会在停止服务前中止升级。使用后删除复制的运行报告。

`initdb` 在新数据库中直接创建当前结构，对已初始化的数据库检查表和字段。本次重构面向全新部署，不提供应用迁移历史或旧部署转换工具。

离线机器通过镜像归档和模块基础设施缓存部署。`parameters.image_archives` 填写绝对归档路径，`parameters.offline: true` 启用离线镜像检查。安装器加载归档并确认所需镜像齐全。离线安装前准备系统软件包和任务运行模块的版本化下载缓存。

```bash
sudo platformctl uninstall --config cluster.yaml --host compute-a \
  --maintenance-reports /tmp/platform-maintenance.json
```

卸载要求维护模式和无活动任务，随后停止 Compose、反序卸载所选模块并保留持久数据。存储维护前，一起备份数据库、`.env`、节点密钥、仓库状态和数据卷。

任务运行和 GPU 模块记录自己创建的主机文件、集群对象和服务。卸载删除这些组件，恢复模块修改的 Docker 和仓库信任配置项，并保留其他配置与已有基础设施。管理员修改过的生成文件会被保留。停止模块创建的 MPS 守护进程后，恢复启动前记录的 GPU 计算模式。K3s 数据、任务命名空间、用户卷和已安装的系统软件包仍保留在磁盘上；模块新建的 K3s 或 LXCFS 服务会被禁用。以后安装时可以重新挂接保留的数据。
