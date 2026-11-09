# 安全边界

学生无宿主机账号和 Kubernetes 凭证。用户 Pod 挂载个人卷、工作卷和只读数据集；SSH 以 UID 1000 登录，支持容器内免密 sudo，继承常规 Docker 默认 capabilities。工作容器使用专用 seccomp 与 AppArmor 策略，允许 bubblewrap 创建内部命名空间；宿主机目录、管理 socket 和 API token 保持隔离。数据集通过管理员创建的只读 Local PV 提供。个人镜像及构建记录按当前登录用户过滤；任务详情及停止接口对他人返回 404。

SSH 仅允许公钥、禁用 root 和密码认证，允许 VS Code 所需的 TCP forwarding，sftp 使用 internal-sftp。公钥由只读任务 Secret 提供，OpenSSH 通过 root 所有的 `/usr/bin/cat` AuthorizedKeysCommand 读取，StrictModes 保持启用。每个任务使用独立 HostKeyAlias，端口复用时仍分别验证主机密钥。私钥保留在用户设备。

容器接受所属用户的启用公钥，以及所有启用中管理员账号的启用公钥。管理员使用目标容器的登录名进入，随后可在容器内 sudo。权限取自账号 `is_staff`，资源不限额用户组只影响额度。同步进程在处理运行容器时重算公钥，仅更新 Secret 的 `authorized_keys`，保留 GPU 租约和环境配置；公钥更新记入审计。公钥停用、账号停用和管理员降级会同步到运行容器，影响更新后的新 SSH 连接。Secret 挂载更新有传播延迟，已有 SSH 会话继续运行。[Kubernetes 更新说明](https://kubernetes.io/docs/concepts/configuration/secret/#using-secrets-as-files-from-a-pod)

当前用户允许门户暂用 HTTP，安全 Cookie 通过 `PLATFORM_HTTPS` 控制。Django DEBUG 在生产保持关闭。未来启用 HTTPS 时设置域名、TLS Secret、https origin、`network.https: true`。

公网接口限制 TCP 6443、10250 和 UDP 8472。防火墙添加带平台标识的 DROP 规则，K3s 启动前恢复规则。UDP 8472 的公网限制已获用户明确授权；本机容器接口保留通信。数据库和 containerd 用于内部访问，学生 Pod 使用独立卷挂载。

Traefik 使用 HTTP 80 入口，关闭 LoadBalancer Service 的额外 NodePort 分配。任务 SSH 按需使用 30022–30121，共 100 个端口。本机 UFW 已启用；K3s 的服务转发经过 NAT 与 FORWARD 规则，实际放行状态需要同时检查 K3s 和 UFW 的规则。

构建任务属于独立信任边界。rootless BuildKit 官方配置可能需要 Unconfined seccomp/AppArmor 及 no-process-sandbox，应单独命名空间部署、无宿主机 socket、无学生卷、仅注入本人的仓库推送凭证，并通过实测验证。GPU 工作容器使用相同的专用工作容器策略。

环境保存由本机 systemd 工作进程执行。网页仅提交带源任务 UUID 的类型化请求；本机进程校验源 Pod、所属用户及容器 ID，再将镜像发布至该用户仓库。常见凭据路径检查覆盖容器可写层，独立挂载中的个人卷、工作卷与 SSH 运行文件不进入镜像。保存操作不提供任意宿主机命令入口。

工作容器策略基于 Moby 官方默认策略，保留受限系统调用及 proc/sysfs 保护，并放行内部命名空间所需的调用。源 seccomp 文件 SHA256 为 `6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`，许可证保存在 `deploy/profiles/LICENSE.moby`。当前交付策略面向 ARM64。

工作环境使用专用 CPU/NVIDIA RuntimeClass。OCI 配置补充本任务 PID/网络命名空间的原生 proc 和只读 sysfs 挂载，以及字符设备 TUN（10:200），供 RootlessKit 创建内部网络；原有设备拒绝规则和 capabilities 保持生效。AppArmor 同时保护原路径与补充路径的全局内核、密钥及 sysfs 接口，允许内部 IPv4/IPv6 网络设置。配置由 `deploy/workspace-runtime.py` 安装。

内层 Docker 以 UID 1000 运行，通过用户命名空间映射内部身份。UID/GID 辅助程序分别使用 `CAP_SETUID`、`CAP_SETGID` 文件能力；容器继续以非 root 身份启动。GPU 由外层 NVIDIA 运行时分配，内层通过启动时生成的 CDI 定义注入设备。平台不挂载宿主机运行时 socket，工作容器不获取 Kubernetes 凭据。个人仓库的独立只读凭据仅挂载给所属用户，复制到临时运行目录；环境保存排除这些挂载。

GPU 份额由主机 MPS v3 在服务端将 namespace 绑定到独立静态 SM 分区；客户端进程数量增加仍共用同一分区。每卡显存硬上限由 NVIDIA 驱动绑定到外层容器 cgroup，包含内层 Rootless Docker 的用量。用户仅挂载本任务的私有 MPS 接入目录，全局控制 socket、租约文件和主机 cgroup 配置留在主机。GPU 计算模式为 EXCLUSIVE_PROCESS，移除 MPS 接入配置后无法另建直接 CUDA 上下文。共享任务仍共用物理卡带宽与设备故障域。

显存上限覆盖常规 CUDA 设备显存分配。NVIDIA 当前将 UVM / `cudaMallocManaged` 计入系统内存，这类分配不具有相同的 GPU 显存封顶保证，系统内存仍受容器 cgroup 限额约束。见 [驱动已知限制](https://docs.nvidia.com/deploy/mps/mpsv3-memory-partitioning.html#known-limitations)。

RootlessKit 使用独立 PID 和网络命名空间，Docker 在内部配置桥接网卡。外层的 LXCFS 资源视图继续供 SSH 会话使用。`keyctl` 返回 `ENOSYS`，让 runc 跳过可选会话 keyring；`add_key` 和 `request_key` 继续禁用。实现使用标准 OCI 挂载与运行时配置。
