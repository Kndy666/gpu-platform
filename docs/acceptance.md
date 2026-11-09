# 验证与自检

2026-11-09 完整回归：267 项自动化测试通过。新增覆盖申请页与 API 余量一致、带 GPU 环境保存和提交程序、工作卷扩缩复制、复制失败保留原卷、临时空间与用户额度、取消清理，以及保存镜像时清除旧 GPU 执行身份。复制脚本实测保留文件内容、权限、硬链接、软链接和稀疏文件。

本轮申请开发环境、运行程序、GPU 环境详情在 1920、1366、768、390 像素完成 12 项浏览器检查，包含 8 次表单提交；无横向溢出和脚本错误。测试使用独立临时数据库。本轮未重装主机，也未向故障中的线上集群部署；干净系统安装仍需在重装后执行。

已有浏览器验收继续覆盖逐卡申请、任务详情、资源概览、用户组增删改、逐卡“最大”选项，以及管理员容器列表与 SSH 详情的桌面和手机布局。

验证环境为 ARM64 Ubuntu 22.04、Linux 5.15.0-202、NVIDIA 620.70.06、Container Toolkit 1.20.1、K3s 和 4 张 RTX 4090 D。业务表按当前模型同步，不保留项目迁移文件和临时结构更新脚本。

## 自动化检查

按 [README](../README.md) 准备 Python 环境后执行：

```bash
DJANGO_DEBUG=1 PLATFORM_HTTPS=0 .venv/bin/python backend/manage.py test tests --noinput
```

覆盖账号权限、用户组、API 与网页共用校验、算力/显存双额度、多卡原子分配、分区回收、部分创建失败回滚、申请审批、镜像构建与回收、卷管理、公用目录和安装配置。

GPU 算力、显存的“最大”分别覆盖用户组总额均分、单卡上限、整数余量、手填值保留、异构显存容量、不限额账号、缺少设备信息与无可用额度。API 接受 `"max"` 并保存整数；GPU 节点工作进程继续接收确定的数值。

管理员 SSH 自动化覆盖启用账号与公钥过滤、跨用户接入、同公钥去重、管理员降级、公钥停用、运行容器同步、GPU 租约字段保留、普通用户隔离，以及网页/API 搜索与权限一致。`tests/e2e/admin_ssh.py` 用临时账号和真实 SSH 验证运行容器接入及撤销，收尾删除这些账号、任务、卷、令牌、队列和审计记录。

本轮实机确认：所属用户正常登录；管理员在容器运行后添加公钥，59.2 秒后可用本人的密钥进入并 sudo；另一普通用户的 API 和 SSH 接入被拒绝。撤销管理员角色后，API 立即拒绝全体容器查询，79.8 秒后新 SSH 连接被拒绝，所属用户继续正常连接。该延迟来自同步周期和 Secret 挂载传播。

线上另完成四卡全选最大、双卡手填与最大混合的两次 API 提交，读取保存结果一致，OpenAPI 同步更新。两条临时申请及其审计和令牌已删除；本轮仅提交待审核申请，未创建测试工作容器和数据卷。

## GPU 接入实机验收

2026-11-06 故障恢复修复：22 项 GPU 运行时与资源计算测试、30 项任务同步与工作流测试通过。覆盖控制进程重启后缺少 `shared` 服务、旧容器 cgroup 尚在、共享服务仍在时的暂时错误，以及回收失败期间继续发布不可用状态。实机恢复四张卡的可分配容量，并运行双卡 25% / 每卡 3 GiB 的 CUDA 程序，确认显存限额、SM 数和任务退出回收正常；临时账号、任务、卷、令牌和队列已清除。

`tests/e2e/gpu_sharing.py --mixed-only` 验证混合规格、逐卡显存和设备顺序；默认完整场景继续覆盖同规格双卡与四卡。该脚本通过平台 API 创建临时普通账号和任务，使用 SSH 验证实际 CUDA 视图；收尾删除该账号、任务、卷、令牌和队列。`gpu_quota.c` 直接使用 CUDA Driver API，默认镜像无需预装 Python 或 CUDA SDK。`nccl_check.c` 使用临时官方 NCCL 2.32.3 与 CUDA runtime 13.5，执行真正的多进程通信。

| 场景 | 结果 |
| --- | --- |
| 不同规格混合申请 | 已有 25% 任务占用 GPU0 时，第一行 100% / 5 GiB 匹配 GPU1，第二行 50% / 3 GiB 复用 GPU0；外层和内层 Docker 均按配置顺序显示 114 SM / 5120 MiB、56 SM / 3072 MiB；时间轴逐卡一致，退出后原任务保持可用 |
| 单卡 25%、3 GiB | CUDA 可见 28 SM，总显存 3072 MiB |
| 同卡已有任务，再申请双卡 50%、每卡 4 GiB | 两张不同物理卡各 56 SM、4096 MiB，原任务继续运行 |
| 外层与 Rootless Docker 共用额度 | 内层自动获得相同 SM 和显存视图；外层持有 2048 MiB 时，内层再申请 1536 MiB 返回 CUDA OOM；释放外层占用后内层可申请 2048 MiB |
| 客户端配置改动 | 改动客户端 SM 参数仍为 28 SM；移除私有 MPS 接入路径后创建 CUDA 上下文失败 |
| 释放单卡任务并新建 35% 任务 | 新任务得到 38 SM，原双卡任务保持有效 |
| 双卡整卡、每卡 3 GiB | 两张卡各 114 SM；两进程 NCCL all-reduce 各得到 3 |
| 四卡共享 50%、每卡 3 GiB | 四张卡各 56 SM；四进程 NCCL all-reduce 各得到 10 |
| 四卡整卡、每卡 3 GiB | 四张卡各 114 SM；四进程 NCCL all-reduce 各得到 10 |
| 双卡共享程序 Job | 两卡各 28 SM、3 GiB，命令成功退出；容器内 sudo 修改显存上限返回 Insufficient Permissions |
| 退出回收 | 全部任务 namespace、SM 分区和租约释放；四卡恢复每卡 114 SM 可分配 |

NCCL 验收覆盖通信层，未重复下载多 GB 的 PyTorch 框架。常规 CUDA 设备显存受硬限额约束；UVM / `cudaMallocManaged` 按驱动规则计入系统内存，详见[安全边界](security.md)。

## 容器与部署检查

`tests/e2e/docker_workspace.py` 验证学生 SSH、Rootless Docker build、Compose、联网、端口发布、只读仓库身份，以及无 GPU 开发环境保存后转 GPU 程序的流程。`workspace_tools.py` 覆盖用户名、sudo、网络与编译工具、bubblewrap、Bash 高亮和 htop 资源视图。默认环境使用 Ubuntu 官方 APT 源，保留唯一默认镜像 Ubuntu 22.04。

```bash
curl --fail "${GPU_PLATFORM_URL}/api/v1/health"
sudo k3s kubectl -n gpu-platform get pods
sudo k3s kubectl -n gpu-workloads get pods,jobs,svc,pvc
sudo systemctl status gpu-platform-node gpu-platform-mps
```

健康接口应返回 Web 和同步进程正常。空闲时用户 Pod/Job/Service 为零；公共数据、LXCFS 和 GPU 接入 PVC 属于基础设施。GPU 租约目录应为空，MPS 分区列表显示每卡 114 SM 未分配。后台 MPS 服务会占用少量驱动显存。

## GPU 共享方案实测

2026-11-05，北京时间。在现有 ARM64、Linux 5.15.0-202、RTX 4090 D 主机上，对比 NVIDIA 驱动显存限额、MPS v3 与 HAMi-core。对比阶段使用驱动 615.71.09；平台接入升级到 620.70.06，Container Toolkit 升至 1.20.1；内核保留 5.15，通过驱动的 misc 后端执行显存限额。HAMi-core 使用上游提交 `17212525d27ac156955ab940c5d51df33d9e0d09`，按其 `build.sh` 构建并开启显存、NVML 和多进程拦截。测试运行在独立命名空间，复用 Ubuntu 22.04 工作镜像和现有 Rootless Docker。

测试使用 CUDA Driver API、持续浮点计算内核和 64 MiB 显存分配块。计算测试预热 2 秒，测量 8–12 秒，GPU0 不限额基线为每秒 7046 次内核执行。以下百分比表示该微基准的吞吐比例；应用实际吞吐和 `nvidia-smi` 的 GPU-Util 各自有不同含义。

| 场景 | 实测结果 |
| --- | --- |
| 官方 cgroup 显存硬上限 3072 MiB | 超额分配返回 CUDA OOM；容器查询总显存为 3072 MiB。扣除 CUDA 上下文后，单进程可分配 2688 MiB。外层已持有 2048 MiB 时，内层 Docker 最多再分配 320 MiB |
| MPS namespace 动态线程上限 | 单进程 50% 配额吞吐为基线的 50.5%，25% 配额为 25.7%；同 namespace 两个 25% 进程合计 51.2%，四个合计 101.8%。该设置按客户端限制，不能单独承担任务合计配额 |
| MPS 固定 SM 分区 | 114 个 SM 中分配 28 个，单进程吞吐 25.7%，四进程合计 26.4%；内层 Docker 单独运行 25.7%，外层与内层同时运行合计 27.0%。所有客户端共用同一物理 SM 分区 |
| 官方显存限额与固定分区组合 | MPS 与 Rootless Docker 同时启用时，外层持有 2048 MiB 后，内层再分配至 256 MiB 即返回 OOM；计算资源仍固定为 28 个 SM |
| 强制接入 MPS | GPU 设为 EXCLUSIVE_PROCESS、MPS 服务运行时，缺少分区配置返回 CUDA 915；移除 MPS 接入变量返回 CUDA 46。测试结束恢复 Default 计算模式 |
| HAMi 单容器 | 3 GiB 显存限额和多进程显存合计限制通过；force 策略下，50% 配额吞吐 56.8%，25% 配额 27.7%，同容器双进程合计 26.7% |
| HAMi 内层 Docker | 显式挂载同一拦截库、同一计账文件并传入配额变量后，内层单进程吞吐 28.3%；外层、内层各分配 2048 MiB 均成功，实际 GPU 显存使用 4807 MiB，超过 3072 MiB 任务总上限 |
| HAMi 嵌套问题定位 | 内层日志出现 `Kick dead proc ... (proc_alive check)`，外层出现 `Process slot not found`。上游存活检查使用当前 PID 命名空间的 `/proc/<pid>/stat`，跨命名空间共用缓存会误清理仍存活的进程记录 |
| HAMi 取消拦截 | 移除 LD_PRELOAD 后可直接分配 6144 MiB，软件配额不再生效 |

当前平台更适合接入“官方 cgroup 显存硬限额 + MPS 固定 SM 分区 + EXCLUSIVE_PROCESS”。它保持原始 CUDA 库，外层与内层 Docker 共用任务额度；HAMi 适合先做普通单层容器共享，现有嵌套 Docker 路径需要额外处理进程身份和计账。MPS 动态线程百分比与固定 SM 分区分别完成实测，推荐使用固定分区。


参考：[NVIDIA 显存分区](https://docs.nvidia.com/deploy/mps/mpsv3-memory-partitioning.html)、[NVIDIA 固定 SM 分区](https://docs.nvidia.com/deploy/mps/latest/when-to-use-mps.html#static-sm-partitioning)、[HAMi-core](https://github.com/Project-HAMi/HAMi-core/tree/17212525d27ac156955ab940c5d51df33d9e0d09)。

## GPU 用户组权限

用户组通过总算力百分比、总显存、单卡算力上限和单卡显存上限配置 GPU 权限。现有整卡总额按 1 卡 = 100% 转换；管理员不限额的全部配额继续为空。转换脚本先在独立旧结构样例库验证，再更新业务库，收尾删除临时脚本。

线上 API 验收使用临时普通账号：总额 75% / 6 GiB、单卡上限 50% / 4 GiB 时，单卡 51%、单卡 5 GiB、总算力 100%、总显存 8 GiB 均被拒绝；50% / 4 GiB 加 25% / 2 GiB 的双卡请求成功提交。将组额度收紧到 50% / 4 GiB、单卡 25% / 2 GiB 后，原待审核申请被拒绝批准。Kueue 用户队列分别读取到 75/6144 和 50/4096 的借用上限。测试账号、用户组、申请、公钥、令牌、队列和审计记录均已清理。
