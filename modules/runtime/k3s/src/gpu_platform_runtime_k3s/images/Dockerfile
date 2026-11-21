ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}
ARG BUILD_APT_MIRROR=
ARG DOCKER_VERSION=29.9.2
ARG CONTAINERD_VERSION=2.4.2-3~ubuntu.22.04~jammy
ARG BUILDX_VERSION=0.38.1-1~ubuntu.22.04~jammy
ARG COMPOSE_VERSION=5.6.1-1~ubuntu.22.04~jammy
ARG NVIDIA_TOOLKIT_VERSION=1.20.1-1
RUN cp /etc/apt/sources.list /tmp/official-sources.list \
    && if [ -n "$BUILD_APT_MIRROR" ]; then sed -i "s@ports.ubuntu.com@${BUILD_APT_MIRROR}@g; s@archive.ubuntu.com@${BUILD_APT_MIRROR}@g" /etc/apt/sources.list; fi \
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       openssh-server ca-certificates sudo curl wget git rsync xz-utils bzip2 unzip zip \
       iproute2 iputils-ping dnsutils net-tools netcat-openbsd traceroute procps lsof jq \
       build-essential cmake ninja-build pkg-config file less vim-tiny bubblewrap htop bash-completion \
    && rm -f /etc/ssh/ssh_host_* \
    && useradd --create-home --uid 1000 --shell /bin/bash user \
    && passwd -d user \
    && printf '#1000 ALL=(ALL:ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/platform-user \
    && chmod 0440 /etc/sudoers.d/platform-user && visudo -cf /etc/sudoers.d/platform-user \
    && mkdir -p /run/sshd /run/user /workspace /datasets /opt/platform \
    && chown user:user /run/user /workspace
COPY vendor/ble-0.4.0-devel3.tar.xz /tmp/blesh.tar.xz
COPY vendor/LICENSE.blesh /usr/share/blesh/LICENSE.md
RUN echo '9c29ed0370c1a7ef2cc13ab35b2b0a30f17d7dd53ec68e21d2821f10ce999866  /tmp/blesh.tar.xz' | sha256sum -c - \
    && mkdir -p /usr/share/blesh && tar -xJf /tmp/blesh.tar.xz -C /usr/share/blesh \
    && rm /tmp/blesh.tar.xz \
    && sed -i 's/^#force_color_prompt=yes/force_color_prompt=yes/' /etc/skel/.bashrc \
    && printf '\nif [[ $- == *i* && -t 0 && -t 1 ]]; then\n  source /usr/share/blesh/ble.sh\nfi\n' >> /etc/skel/.bashrc
RUN set -eu; \
    if [ -n "$BUILD_APT_MIRROR" ]; then sed -i "s@ports.ubuntu.com@${BUILD_APT_MIRROR}@g; s@archive.ubuntu.com@${BUILD_APT_MIRROR}@g" /etc/apt/sources.list; fi; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL --retry 3 --retry-all-errors --connect-timeout 10 --max-time 60 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc; \
    curl -fsSL --retry 3 --retry-all-errors --connect-timeout 10 --max-time 60 https://nvidia.github.io/libnvidia-container/gpgkey -o /etc/apt/keyrings/nvidia.asc; \
    arch="$(dpkg --print-architecture)"; \
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable\n' "$arch" > /etc/apt/sources.list.d/docker.list; \
    printf 'deb [signed-by=/etc/apt/keyrings/nvidia.asc] https://nvidia.github.io/libnvidia-container/stable/deb/%s /\n' "$arch" > /etc/apt/sources.list.d/nvidia-container-toolkit.list; \
    apt-get -o Acquire::Retries=3 update; \
    DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
      "docker-ce=5:${DOCKER_VERSION}-1~ubuntu.22.04~jammy" \
      "docker-ce-cli=5:${DOCKER_VERSION}-1~ubuntu.22.04~jammy" \
      "docker-ce-rootless-extras=5:${DOCKER_VERSION}-1~ubuntu.22.04~jammy" \
      "containerd.io=${CONTAINERD_VERSION}" "docker-buildx-plugin=${BUILDX_VERSION}" \
      "docker-compose-plugin=${COMPOSE_VERSION}" "nvidia-container-toolkit-base=${NVIDIA_TOOLKIT_VERSION}" \
      uidmap libcap2-bin slirp4netns iptables nftables; \
    chmod 0755 /usr/bin/newuidmap /usr/bin/newgidmap; \
    setcap cap_setuid=ep /usr/bin/newuidmap; \
    setcap cap_setgid=ep /usr/bin/newgidmap; \
    mv /tmp/official-sources.list /etc/apt/sources.list; \
    rm -rf /var/lib/apt/lists/* /var/lib/docker /var/lib/containerd
COPY --chmod=755 runner.sh /opt/platform/runner.sh
COPY --chmod=755 docker.sh /opt/platform/docker.sh
USER 1000:1000
ENV HOME=/home/user LANG=C.UTF-8
WORKDIR /workspace
EXPOSE 2222
ENTRYPOINT ["/bin/bash", "/opt/platform/runner.sh", "/run/platform/task.env"]
