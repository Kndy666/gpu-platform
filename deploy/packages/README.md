# LXCFS CPU 视图修补包

`lxcfs_5.0.0-0ubuntu2+platform2_arm64.deb` 用于 Ubuntu 22.04 ARM64。安装器按 `deploy/patches/lxcfs-packages.json` 中的 SHA256 校验后安装，提供容器按 CPU 配额显示的 `/proc` 视图。包内保留 Ubuntu LXCFS 的版权与许可证文件。

`source/` 包含对应的 LXCFS 5.0.0 源码、Ubuntu `5.0.0-0ubuntu2` Debian 补丁和原始包；源码归档内保留 `COPYING` 许可证。平台改动位于 `deploy/patches/lxcfs-5.0.0-cgroup2-cpu-count.patch`。执行 `sudo ./deploy/build-resource-view.sh /tmp/lxcfs-build` 在当前主机架构的 Ubuntu 22.04 Docker 中重建，输出位于指定目录；第二个参数可指定 `docker.m.daocloud.io/library/ubuntu:24.04`。构建脚本校验全部输入的 SHA256，原始包提供打包元数据和许可证，二进制与共享库按当前架构重新编译。原始文件来自：

- https://linuxcontainers.org/downloads/lxcfs/lxcfs-5.0.0.tar.gz
- https://archive.ubuntu.com/ubuntu/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2.debian.tar.xz
- https://ports.ubuntu.com/ubuntu-ports/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2_arm64.deb

ARM64 安装器使用本目录中的已校验修补包。AMD64 安装器在临时目录构建对应 Ubuntu 版本的包、安装并清理构建目录。更新修补包前先停止该节点用户容器。
