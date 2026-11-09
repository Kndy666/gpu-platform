# LXCFS CPU 视图修补包

`lxcfs_5.0.0-0ubuntu2+platform2_arm64.deb` 用于 Ubuntu 22.04 ARM64。安装器按 `deploy/patches/lxcfs-packages.json` 中的 SHA256 校验后安装，提供容器按 CPU 配额显示的 `/proc` 视图。包内保留 Ubuntu LXCFS 的版权与许可证文件。

`source/` 包含对应的 LXCFS 5.0.0 源码、Ubuntu `5.0.0-0ubuntu2` Debian 补丁和原始包；源码归档内保留 `COPYING` 许可证。平台改动位于 `deploy/patches/lxcfs-5.0.0-cgroup2-cpu-count.patch`。执行 `sudo ./deploy/build-resource-view.sh /tmp/lxcfs-build` 可在 Ubuntu 22.04 ARM64 Docker 中重建，输出位于指定目录。构建脚本校验全部输入的 SHA256。原始文件来自：

- https://linuxcontainers.org/downloads/lxcfs/lxcfs-5.0.0.tar.gz
- https://archive.ubuntu.com/ubuntu/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2.debian.tar.xz
- https://ports.ubuntu.com/ubuntu-ports/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2_arm64.deb

完整部署直接使用仓库中的修补包，无需先生成旧工作镜像或从旧服务器复制 `dist` 目录。
