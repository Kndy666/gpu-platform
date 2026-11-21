# LXCFS resource-view package

This directory supplies quota-aware CPU views for the runtime. ARM64 installation uses `lxcfs_5.0.0-0ubuntu2+platform2_arm64.deb` for Ubuntu 22.04, verified against [the package manifest](../patches/lxcfs-packages.json). AMD64 installation builds the matching Ubuntu package in a temporary directory and removes it afterward.

## Rebuild

With Docker access, run from this directory:

```bash
../build-resource-view.sh /tmp/lxcfs-build
```

An optional second argument selects the Ubuntu build image. The script verifies input digests and retains package metadata and licenses. Finish running workspaces before replacing the resource-view service.

## Sources and notices

`source/` contains LXCFS 5.0.0, the Ubuntu `5.0.0-0ubuntu2` Debian patches, and the original package. The archive retains `COPYING`, and the package retains Ubuntu copyright and license files. The local CPU-count change is [the cgroup2 patch](../patches/lxcfs-5.0.0-cgroup2-cpu-count.patch).

- [LXCFS 5.0.0 source](https://linuxcontainers.org/downloads/lxcfs/lxcfs-5.0.0.tar.gz)
- [Ubuntu Debian patches](https://archive.ubuntu.com/ubuntu/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2.debian.tar.xz)
- [Ubuntu ARM64 package](https://ports.ubuntu.com/ubuntu-ports/pool/universe/l/lxcfs/lxcfs_5.0.0-0ubuntu2_arm64.deb)
