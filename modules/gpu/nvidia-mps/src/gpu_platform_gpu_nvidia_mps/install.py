import shlex
import subprocess
import json
import os
from pathlib import Path

from gpu_platform_contracts.host import HostOwnership
from gpu_platform_contracts.handoffs import GPURuntime
from gpu_platform_contracts.module import CheckResult
from gpu_platform_contracts.texts import Message


def check_host(context):
    try:
        result = context.runner(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True
        )
        versions = [
            tuple(int(part) for part in line.strip().split(".")) for line in result.stdout.splitlines()
        ]
        ok = bool(versions) and all(version >= (620, 70, 6) for version in versions)
    except (OSError, ValueError, subprocess.SubprocessError):
        ok = False
    return [
        CheckResult(ok=ok, message=Message(code="gpu.driver_required", parameters={"minimum": "620.70.06"}))
    ]


def install_host(context):
    owned = HostOwnership(context, "gpu-host")
    with owned.files([
        Path("/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"),
        Path("/etc/apt/sources.list.d/nvidia-container-toolkit.list"),
        context.state_directory / "gpu" / "oci-hook",
    ]):
        context.runner(["bash", str(Path(__file__).parent / "host" / "toolkit.sh")])
        with HostOwnership(context, "gpu-docker").configuration(Path("/etc/docker/daemon.json")):
            context.runner(["nvidia-ctk", "runtime", "configure", "--runtime=docker"])
        install_wrapper(context)
    context.runner(["systemctl", "restart", "docker"])
    context.installation_handoffs.setdefault("gpu_allocation", []).append(
        GPURuntime(form="nvidia-mps", node_id=context.node_id, binary="/usr/bin/nvidia-container-runtime",
                   prestart_hook=str(context.state_directory / "gpu" / "oci-hook")).model_dump(mode="json")
    )


def install_wrapper(context):
    root = context.state_directory / "gpu"
    root.mkdir(mode=0o711, parents=True, exist_ok=True)
    wrapper = root / "oci-hook"
    command = [
        "docker",
        "exec",
        "-i",
        "-e",
        f"PLATFORM_STATE_DIRECTORY={context.state_directory}",
        context.options.get("node_container_name", "gpu-platform-node"),
        "python",
        "-m",
        "gpu_platform_gpu_nvidia_mps.module",
        "hook",
    ]
    wrapper.write_text("#!/bin/sh\nexec " + shlex.join(command) + "\n")
    wrapper.chmod(0o755)


def uninstall_host(context):
    root = context.state_directory / "gpu"
    if any((root / "leases").glob("*.json")):
        raise RuntimeError("gpu.active_leases_prevent_uninstall")
    ownership = root / "server-ownership.json"
    if ownership.exists():
        record = json.loads(ownership.read_text())
        env = {**os.environ, "CUDA_MPS_PIPE_DIRECTORY": str(root / "pipes"),
               "CUDA_MPS_LOG_DIRECTORY": str(root / "logs")}
        if record.get("daemon"):
            result = context.runner(["nvidia-cuda-mps-control", "-p", "3", "-q"], env=env, check=False)
            if result.returncode:
                probe = context.runner(
                    ["nvidia-cuda-mps-control", "-p", "3", "server", "list", "--format=csv,noheader"],
                    env=env, check=False, capture_output=True,
                )
                if probe.returncode == 0:
                    raise RuntimeError("gpu.daemon_shutdown_failed")
        elif record.get("server"):
            context.runner(["nvidia-cuda-mps-control", "-p", "3", "server", "delete", "shared"],
                           env=env)
        for device, mode in record.get("compute_modes", {}).items():
            result = context.runner(["nvidia-smi", "-i", device, "--query-gpu=compute_mode",
                                     "--format=csv,noheader"], capture_output=True)
            desired = record.get("transition") or ("DEFAULT" if record.get("isolated") else "EXCLUSIVE_PROCESS")
            applied = {"default", "0"} if desired == "DEFAULT" else {"exclusive_process", "3"}
            if result.stdout.strip().lower().replace(" ", "_") in applied:
                context.runner(["nvidia-smi", "-i", device, "-c", str(mode)])
        ownership.unlink()
    if HostOwnership(context, "gpu-docker").cleanup():
        context.runner(["systemctl", "restart", "docker"])
    HostOwnership(context, "gpu-host").cleanup()


def compose(context):
    return {
        "services": {
            "node": {
                "container_name": context.options.get("node_container_name", "gpu-platform-node"),
                "cgroup": "host",
                "gpus": "all",
                "environment": {"NVIDIA_DRIVER_CAPABILITIES": "compute,utility"},
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
            }
        }
    }
