# 权限与运行配置

## 账号与 SSH

普通用户操作本人的任务、私有镜像、数据卷、公钥和令牌。管理员可管理全部任务、镜像和数据卷，成员与用户组管理使用账号角色权限。不限额用户组控制资源额度，`is_staff` 控制管理权限。

SSH 使用公钥认证，登录账号与任务创建时的网站用户名一致。容器内 UID/GID 为 1000，支持免密 sudo。每份任务使用独立的 HostKeyAlias 和 SSH 端口，支持 SFTP、TCP forwarding 与 VS Code Remote-SSH。

容器接受所属用户及启用中管理员账号的启用公钥。公钥通过任务 Secret 提供，OpenSSH 在新连接时读取。账号、公钥和角色变更经同步进程更新到运行容器；Secret 挂载传播完成后影响新连接，已有会话继续运行。[Kubernetes Secret 更新机制](https://kubernetes.io/docs/concepts/configuration/secret/#using-secrets-as-files-from-a-pod)

## 网络

| 入口 | 默认端口 | 配置 |
| --- | --- | --- |
| 网页与 API | TCP 80 | Traefik HTTP |
| HTTPS | TCP 443 | `network.https`、域名与 TLS Secret |
| 容器 SSH | TCP 30022–30121 | `platform.ssh_ports`，按任务分配 |
| Kubernetes API | TCP 6443 | 集群内部使用 |
| kubelet | TCP 10250 | 集群内部使用 |
| Flannel VXLAN | UDP 8472 | 节点网络通信 |

安装器在公网接口过滤 6443、10250 和 8472，规则随主机启动恢复。K3s 的 Service 转发同时经过 NAT 与 FORWARD 链，排查连通性时检查平台规则和 UFW。

`platform.https_origin` 设置网页地址，`network.https` 控制 HTTPS。生产环境关闭 Django DEBUG；Cookie 的 Secure 属性由 `PLATFORM_HTTPS` 控制。

## 工作容器与 Docker

工作容器继承常规 Docker 默认 capabilities，使用仓库中的 seccomp 和 AppArmor 策略。策略支持 bubblewrap、内部命名空间与 Rootless Docker，配置源和许可证位于 `deploy/profiles/`。

CPU 与 GPU 环境分别使用 `workspace-runc` 和 `workspace-nvidia` RuntimeClass。运行时为内部命名空间提供 proc、只读 sysfs 和 TUN 设备；配置由 `deploy/workspace-runtime.py` 安装。

内层 Docker 以 UID 1000 运行，通过用户命名空间映射内部身份。Docker 镜像、容器层、缓存和命名卷保存在 `/workspace/.docker`。内外层共同使用外层任务的 CPU、内存与 GPU 额度，LXCFS 为 SSH 会话提供额度视图。

| 资源 | 执行机制 | 显示与使用 |
| --- | --- | --- |
| CPU | cgroup `cpu.max` | LXCFS 显示申请核数，`cpu.stat` 记录实际用量 |
| 内存 | 容器 cgroup | LXCFS 提供申请额度对应的内存视图 |
| GPU 算力 | MPS v3 服务端绑定静态 SM 分区 | 任务的所有进程及内层容器共用分区 |
| GPU 显存 | NVIDIA 驱动逐卡 cgroup 上限 | 常规 CUDA 设备分配达到上限时返回 OOM |

SM 份额决定可用计算单元，程序吞吐还取决于显存带宽和计算特征。同卡任务共用物理带宽与设备故障域。UVM / `cudaMallocManaged` 按 NVIDIA 驱动规则计入系统内存，受容器内存额度约束。[NVIDIA 内存计账说明](https://docs.nvidia.com/deploy/mps/mpsv3-memory-partitioning.html#known-limitations)

GPU 租约与全局 MPS 控制文件由主机 root 管理，工作容器挂载本任务的私有 MPS 接入目录。GPU 使用 `EXCLUSIVE_PROCESS` 模式，由主机 MPS 服务持有计算上下文。

## 镜像与数据

工作容器挂载个人卷、工作卷和只读公用数据，平台管理凭据留在应用与主机服务。容器内可拉取共享镜像及本人的私有镜像，仓库登录配置使用个人只读身份。

Dockerfile 构建位于独立命名空间，Rootless BuildKit 使用专用临时卷和所属用户的推送凭据。BuildKit 容器配置 Unconfined seccomp / AppArmor 与 `no-process-sandbox`；用户工作容器使用专用工作策略。

环境保存请求携带源任务 UUID。节点进程校验源 Pod、用户和容器 ID，将可写层推送到所属用户仓库。个人卷、工作卷、SSH 运行文件与仓库凭据作为独立挂载保存。

公用目录网页浏览通过文件描述符和 `O_NOFOLLOW` 逐级打开路径，下载采用流式附件响应。文件上传通过服务器 SSH 完成。
