# LXCFS CPU resource view

`lxcfs_5.0.0-0ubuntu2+platform2_arm64.deb` supplies quota-aware CPU views for Ubuntu 22.04 ARM64. The runtime installer verifies it against `../patches/lxcfs-packages.json`. The package retains Ubuntu's copyright and license files.

`source/` contains LXCFS 5.0.0, the Ubuntu `5.0.0-0ubuntu2` Debian patches, and the original package. The source archive retains its `COPYING` file. The local CPU-count change is `../patches/lxcfs-5.0.0-cgroup2-cpu-count.patch`.

Run `../build-resource-view.sh /tmp/lxcfs-build` with Docker access to rebuild for the current host architecture. Its optional second argument selects the Ubuntu build image. The script verifies input digests, reuses package metadata and licenses, and rebuilds the executable and shared library for the target architecture.

Source artifacts:

- https://linuxcontainers.org/downloads/lxcfs/lxcfs-5.0.0.tar.gz
- https://archive.ubuntu.com/ubuntu/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2.debian.tar.xz
- https://ports.ubuntu.com/ubuntu-ports/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2_arm64.deb

ARM64 installation uses the verified package in this directory. AMD64 installation builds the matching Ubuntu package in a temporary directory and removes that directory afterward. Finish running workspaces before changing the installed resource-view service.
