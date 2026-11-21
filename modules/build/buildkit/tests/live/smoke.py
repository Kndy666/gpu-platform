#!/usr/bin/env python3
"""Opt-in isolated Docker checks for queueing, limits, OCI output and private FROM."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import requests

from gpu_platform_build_buildkit.module import Module as BuildKit
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.slots.build import BuildSpec
from gpu_platform_registry_zot.module import Module as Registry


def run(arguments, **options):
    return subprocess.run(
        arguments, **{"check": True, "capture_output": True, "text": True, "timeout": 180, **options}
    )


def docker(*arguments):
    return run(["docker", *arguments])


def wait_registry(url, ca, timeout=30):
    deadline = time.monotonic() + timeout
    with requests.Session() as session:
        session.trust_env = False
        while time.monotonic() < deadline:
            try:
                response = session.get(url + "/v2/", verify=ca, timeout=2)
                if response.status_code in {200, 401, 403}:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.2)
    raise TimeoutError("Isolated registry did not become ready")


def wait_build(builder, identifier, timeout=180):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        result = builder.status(identifier)
        if result.state != previous:
            print(f"Build {identifier}: {result.state}", flush=True)
            previous = result.state
        if result.state == "failed":
            raise AssertionError(f"Build failed: {result.error}\n{result.logs}")
        if result.state == "completed":
            assert result.image and result.image.size_bytes > 0
            return result.image
        time.sleep(0.5)
    raise TimeoutError(f"Build timed out: {identifier}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-docker", action="store_true", required=True)
    parser.add_argument("--builder-image", default="moby/buildkit:v0.26.1-rootless")
    parser.add_argument("--registry-image")
    parser.add_argument("--base-image", default="busybox:1.37.0")
    parser.add_argument("--environment-context", type=Path)
    parser.add_argument("--environment-output", type=Path)
    parser.add_argument("--environment-base", default="ubuntu:22.04")
    parser.add_argument("--apt-mirror", default="")
    arguments = parser.parse_args()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    identity = "gpu-platform-build-smoke-" + uuid4().hex[:12]
    registry_name, builder_name = identity + "-registry", identity + "-builder"
    network, volume = identity + "-network", identity + "-cache"
    registry_image = arguments.registry_image or f"ghcr.io/project-zot/zot-linux-{architecture}:v2.1.22"
    builder = None
    with tempfile.TemporaryDirectory(prefix=identity + "-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        try:
            docker("network", "create", network)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                published_port = listener.getsockname()[1]
            port = 5000
            url = f"https://127.0.0.1:{published_port}"
            registry_context = ModuleContext(
                node_id=identity,
                state_directory=root,
                options={
                    "url": url,
                    "port": port,
                    "architecture": architecture,
                    "container_name": registry_name,
                },
            )
            Registry.install(SimpleNamespace(**registry_context.model_dump(), runner=run))
            ca = str(root / "registry/tls.crt")
            docker(
                "run",
                "--detach",
                "--name",
                registry_name,
                "--network",
                network,
                "--network-alias",
                "registry",
                "--publish",
                f"127.0.0.1:{published_port}:{port}",
                "--volume",
                f"{root}/registry:/etc/zot:ro",
                "--volume",
                f"{root}/registry:/var/lib/registry",
                registry_image,
                "serve",
                "/etc/zot/config.json",
            )
            wait_registry(url, ca)

            def reload_registry():
                docker("restart", registry_name)
                wait_registry(url, ca)

            registry = Registry(registry_context, reload=reload_registry)
            context = ModuleContext(
                node_id=identity,
                state_directory=root,
                options={
                    "container_name": builder_name,
                    "max_cpu": 2,
                    "max_memory_gib": 4,
                    "trusted_contexts": {"payload": str(root / "payload")},
                },
            )
            payload = root / "payload"
            payload.mkdir()
            (payload / "marker").write_bytes(b"portable-build-fixture\x00\xff")
            BuildKit.install(context)
            docker("volume", "create", volume)
            docker(
                "run",
                "--detach",
                "--name",
                builder_name,
                "--network",
                network,
                "--user",
                "1000:1000",
                "--security-opt",
                "seccomp=unconfined",
                "--security-opt",
                "apparmor=unconfined",
                "--security-opt",
                "systempaths=unconfined",
                "--volume",
                f"{root}/buildkit/jobs:/builds",
                "--volume",
                f"{root}/buildkit/config:/buildkit-config:ro",
                "--volume",
                f"{volume}:/home/user/.local/share/buildkit",
                arguments.builder_image,
                "--config",
                "/buildkit-config/buildkitd.toml",
            )
            builder = BuildKit(context, background=False)
            first = BuildSpec(
                id="source",
                owner="source-owner",
                architecture=architecture,
                description=f"FROM {arguments.base_image}\nRUN sleep 12 && printf proof > /proof\n",
                resources={"cpu": "0.5", "memory": 1},
            )
            assert builder.build(first).state == "running"
            host = json.loads(docker("inspect", builder_name).stdout)[0]["HostConfig"]
            assert host["NanoCpus"] == 500_000_000
            assert host["Memory"] == host["MemorySwap"] == 2**30
            second = BuildSpec(
                id="queued",
                owner="queued-owner",
                architecture=architecture,
                context="trusted:payload",
                description="FROM scratch\nCOPY marker /marker\n",
                resources={"cpu": "0.75", "memory": 1},
            )
            assert builder.build(second).state == "queued"
            builder.close()
            builder = BuildKit(context, background=False)
            source = wait_build(builder, first.id)
            wait_build(builder, second.id)
            print("Durable serial queue and Docker CPU/memory limits: PASS", flush=True)
            stored = registry.store(source, first.owner, "private-source")
            private = registry.get(stored.id, first.owner)
            endpoint = url + "/v2/" + private.metadata["repository"] + "/manifests/" + private.digest
            with requests.Session() as session:
                session.trust_env = False
                assert session.get(endpoint, verify=ca, timeout=10).status_code in {401, 403}
            granted = registry.build_images("recipient", [private], "private-child")
            assert private.reference.startswith(f"127.0.0.1:{published_port}/")
            assert granted[0].metadata["registry_mirrors"] == ["registry:5000"]
            child = BuildSpec(
                id="private-child",
                owner="recipient",
                architecture=architecture,
                images=granted,
                resources={"cpu": 1, "memory": 1},
                description=f"FROM {private.reference}\nRUN test -f /proof && printf child > /child\n",
            )
            builder.build(child)
            wait_build(builder, child.id)
            assert not (builder.directory(child.id) / "auth").exists()
            registry.release_build_access(child.id)
            credentials = granted[0].credentials
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    endpoint, verify=ca, timeout=10, auth=(credentials["username"], credentials["password"])
                )
                assert response.status_code in {401, 403}
            print("Advertised loopback FROM through TLS bridge mirror, denied anonymous pull and grant revocation: PASS", flush=True)
            if arguments.environment_context:
                assets = arguments.environment_context.resolve()
                context.options["trusted_contexts"]["environment"] = str(assets)
                recipe = (assets / "Dockerfile").read_text()
                recipe = recipe.replace(
                    "ARG BASE_IMAGE=ubuntu:22.04", "ARG BASE_IMAGE=" + arguments.environment_base
                )
                recipe = recipe.replace(
                    "ARG BUILD_APT_MIRROR=", "ARG BUILD_APT_MIRROR=" + arguments.apt_mirror
                )
                environment = BuildSpec(
                    id="standard-environment",
                    owner="shared",
                    architecture=architecture,
                    context="trusted:environment",
                    description=recipe,
                    resources={"cpu": 2, "memory": 4},
                )
                builder.build(environment)
                result = wait_build(builder, environment.id, timeout=3600)
                if arguments.environment_output:
                    arguments.environment_output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(result.reference, arguments.environment_output)
                print("Standard environment OCI archive: PASS", flush=True)
        finally:
            if builder:
                builder.close()
            for name in (builder_name, registry_name):
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=60)
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True, timeout=60)
            subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=60)


if __name__ == "__main__":
    main()
