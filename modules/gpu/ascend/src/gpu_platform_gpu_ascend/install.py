"""Install the official Ascend OCI runtime and publish its host handoff."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from gpu_platform_contracts.handoffs import GPURuntime
from gpu_platform_contracts.host import HostOwnership
from gpu_platform_contracts.module import CheckResult
from gpu_platform_contracts.texts import Message

VERSION = "26.1.0"
DIGEST = "0833404fa6fd1ce3e43b8530b4791181bd3e49393a1f547c37eea108c2c3b58c"
ROOT = Path("/usr/local/Ascend/Ascend-Docker-Runtime")
BINARY = ROOT / "ascend-docker-runtime"
CONFIG = Path("/etc/ascend-docker-runtime.d/base.list")
DOCKER = Path("/etc/docker/daemon.json")
UTILITY = Path("/usr/local/bin/npu-smi")


def check_host(context):
    ok = context.host["architecture"] == "arm64"
    try:
        result = context.runner(["npu-smi", "info", "-m"], capture_output=True)
        ok = ok and "Ascend 310P" in result.stdout
    except (OSError, subprocess.SubprocessError):
        ok = False
    return [CheckResult(ok=ok, message=Message(code="ascend.driver_required"))]


def install_host(context):
    files = [
        ROOT / name
        for name in [
            "ascend-docker-runtime",
            "ascend-docker-cli",
            "ascend-docker-hook",
            "ascend-docker-destroy",
            "ascend-docker-plugin-install-helper",
            "README.md",
            "script/uninstall.sh",
            "ascend_docker_runtime_install.info",
            "assets/20230118566.png",
            "assets/20210329102949456.png",
        ]
    ]
    owned = HostOwnership(context, "ascend-host")
    with owned.files([*files, CONFIG, UTILITY]):
        if not BINARY.exists():
            cache = context.state_directory / "cache" / "ascend"
            cache.mkdir(parents=True, exist_ok=True)
            package = cache / f"Ascend-docker-runtime_{VERSION}_linux-aarch64.run"
            if not package.exists() or hashlib.sha256(package.read_bytes()).hexdigest() != DIGEST:
                url = f"https://gitcode.com/Ascend/mind-cluster/releases/download/v{VERSION}/{package.name}"
                temporary = package.with_suffix(".part")
                context.runner(
                    ["curl", "--fail", "--location", "--retry", "3", "--output", str(temporary), url]
                )
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != DIGEST:
                    temporary.unlink()
                    raise ValueError("Ascend runtime checksum does not match")
                temporary.replace(package)
            prior = json.loads(DOCKER.read_text()) if DOCKER.exists() else {}
            with HostOwnership(context, "ascend-docker").configuration(DOCKER):
                context.runner(["bash", str(package), "--install", "--install-scene=docker"], input="Y\n")
                current = json.loads(DOCKER.read_text())
                if "default-runtime" in prior:
                    current["default-runtime"] = prior["default-runtime"]
                else:
                    current.pop("default-runtime", None)
                DOCKER.write_text(json.dumps(current, indent=2) + "\n")
            context.runner(["systemctl", "restart", "docker"])
        if not UTILITY.exists():
            source = shutil.which("npu-smi")
            if not source:
                raise FileNotFoundError("ascend.driver_required")
            context.runner(["install", "-m", "0755", source, str(UTILITY)])
    context.installation_handoffs.setdefault("gpu_allocation", []).append(
        GPURuntime(form="ascend-device", node_id=context.node_id, binary=str(BINARY)).model_dump(mode="json")
    )


def uninstall_host(context):
    if any((context.state_directory / "ascend" / "leases").glob("*.json")):
        raise RuntimeError("ascend.active_leases_prevent_uninstall")
    HostOwnership(context, "ascend-host").cleanup()
    if HostOwnership(context, "ascend-docker").cleanup():
        context.runner(["systemctl", "restart", "docker"])


def compose(context):
    return {
        "services": {
            "node": {
                "volumes": [
                    "/usr/local/Ascend/driver:/usr/local/Ascend/driver:ro",
                    f"{UTILITY}:{UTILITY}:ro",
                ],
                "environment": {
                    "LD_LIBRARY_PATH": "/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common"
                },
            }
        }
    }
