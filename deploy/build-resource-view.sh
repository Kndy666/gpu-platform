#!/usr/bin/env bash
# Build the Ubuntu LXCFS package with the upstream cgroup v2 CPU count fix.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${1:?Provide an empty temporary build directory}"
mkdir -p "${build_dir}"
build_dir="$(cd -- "${build_dir}" && pwd)"
build_image="${2:-docker.m.daocloud.io/library/ubuntu:22.04}"
cp "${project_root}/deploy/packages/source/"* "${build_dir}/"
printf '%s  %s\n' \
  d131b2284437165eabdcdf3c392b96fc95e6ee4f7bf04a7e9999f63997a995ee "$build_dir/lxcfs-5.0.0.tar.gz" \
  200212ca04b437750fbc3ab57b3d5235936399fbc90c3eda8e9cbfcae8841037 "$build_dir/debian.tar.xz" \
  be4431b2635e0bf180f536c4573e97e1980de45f2027d50d5fadecea4f4aef6d "$build_dir/lxcfs-original.deb" | sha256sum --check
docker run --rm --interactive --name platform-lxcfs-cpu-build --user 0:0 --entrypoint /bin/bash \
  --mount "type=bind,src=${build_dir},dst=/build" \
  --mount "type=bind,src=${project_root}/deploy/patches,dst=/patches,readonly" \
  "${build_image}" -s <<'BUILD'
set -euo pipefail
apt-get update >/build/dependencies.log 2>&1
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential meson ninja-build pkg-config python3-jinja2 libfuse-dev patch >>/build/dependencies.log 2>&1
test "$(dpkg-deb -f /build/lxcfs-original.deb Version)" = 5.0.0-0ubuntu2
rm -rf /build/lxcfs-5.0.0 /build/compiled /build/package
tar -xzf /build/lxcfs-5.0.0.tar.gz -C /build
tar -xf /build/debian.tar.xz -C /build/lxcfs-5.0.0
cd /build/lxcfs-5.0.0
while IFS= read -r patch_name; do
  [[ -z "$patch_name" || "$patch_name" == \#* ]] || patch -p1 < "debian/patches/$patch_name"
done < debian/patches/series
patch -p1 < /patches/lxcfs-5.0.0-cgroup2-cpu-count.patch
multiarch="$(gcc -dumpmachine)"
architecture="$(dpkg --print-architecture)"
meson setup /build/compiled --prefix=/usr --libdir="lib/$multiarch" -Dtests=false -Ddocs=false -Dinit-script=sysvinit
ninja -C /build/compiled liblxcfs.so lxcfs
strip --strip-unneeded /build/compiled/liblxcfs.so
rm -rf /build/package
dpkg-deb -R /build/lxcfs-original.deb /build/package
install -d "/build/package/usr/lib/$multiarch/lxcfs"
install -m 0644 /build/compiled/liblxcfs.so "/build/package/usr/lib/$multiarch/lxcfs/liblxcfs.so"
install -m 0755 /build/compiled/lxcfs /build/package/usr/bin/lxcfs
if [[ "$architecture" != arm64 ]]; then
  rm -rf /build/package/usr/lib/aarch64-linux-gnu
fi
sed -i "s/^Architecture: .*/Architecture: ${architecture}/" /build/package/DEBIAN/control
sed -i 's/^Version: .*/Version: 5.0.0-0ubuntu2+platform2/' /build/package/DEBIAN/control
rm -f /build/package/DEBIAN/preinst
cat > /build/package/DEBIAN/postinst <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = configure ] && [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable lxcfs.service
    systemctl start lxcfs.service
fi
POSTINST
cat > /build/package/DEBIAN/prerm <<'PRERM'
#!/bin/sh
set -e
if [ "$1" = remove ] && [ -d /run/systemd/system ]; then
    systemctl stop lxcfs.service
    systemctl disable lxcfs.service
fi
PRERM
cat > /build/package/DEBIAN/postrm <<'POSTRM'
#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then systemctl daemon-reload; fi
POSTRM
chmod 0755 /build/package/DEBIAN/postinst /build/package/DEBIAN/prerm /build/package/DEBIAN/postrm
cd /build/package
find usr -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums
dpkg-deb --build --root-owner-group /build/package "/build/lxcfs_5.0.0-0ubuntu2+platform2_${architecture}.deb"
BUILD
