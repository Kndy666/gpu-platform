import json
import subprocess
from types import SimpleNamespace

import pytest

from gpu_platform_contracts.module import ModuleContext
from gpu_platform_gpu_nvidia_mps.install import uninstall_host
from gpu_platform_gpu_nvidia_mps.install import compose
from gpu_platform_gpu_nvidia_mps.module import Module


def test_private_daemon_uses_v3_multiuser_and_uninstall_restores_original_compute_mode(tmp_path):
    commands = []

    def driver(args, **kwargs):
        commands.append(args)
        if args[3:5] == ["server", "list"]:
            raise subprocess.CalledProcessError(1, args)
        if "--query-gpu=compute_mode" in args:
            return "Exclusive_Process\n"
        return ""

    module = Module(ModuleContext(state_directory=tmp_path),
                    inventory=lambda: [{"uuid": "gpu-a"}], run=driver)
    module.ensure_server()
    assert ["nvidia-cuda-mps-control", "-d", "-p", "3", "-m"] in commands
    assert ["nvidia-smi", "-i", "gpu-a", "-c", "DEFAULT"] in commands
    ownership = module.root / "server-ownership.json"
    assert json.loads(ownership.read_text())["compute_modes"] == {"gpu-a": 3}

    def host(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout="Default\n", returncode=0)

    context = SimpleNamespace(state_directory=tmp_path, runner=host)
    uninstall_host(context)
    assert ["nvidia-cuda-mps-control", "-p", "3", "-q"] in commands
    assert ["nvidia-smi", "-i", "gpu-a", "-c", "3"] in commands
    assert not ownership.exists()
    count = len(commands)
    uninstall_host(context)
    assert len(commands) == count


def test_uninstall_refuses_live_leases_and_preserves_unowned_server(tmp_path):
    root = tmp_path / "gpu" / "leases"
    root.mkdir(parents=True)
    lease = root / "active.json"
    lease.write_text("{}")
    commands = []
    context = SimpleNamespace(state_directory=tmp_path, runner=lambda *a, **kw: commands.append(a))
    with pytest.raises(RuntimeError):
        uninstall_host(context)
    assert not commands and lease.exists()
    lease.unlink()
    uninstall_host(context)
    assert not commands


def test_failed_daemon_shutdown_keeps_recovery_record_and_compute_mode(tmp_path):
    root = tmp_path / "gpu"
    root.mkdir()
    record = root / "server-ownership.json"
    record.write_text(json.dumps({"daemon": True, "compute_modes": {"gpu-a": 0}}))
    commands = []

    def runner(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=1 if "-q" in args else 0, stdout="shared,Ready\n")

    with pytest.raises(RuntimeError):
        uninstall_host(SimpleNamespace(state_directory=tmp_path, runner=runner))
    assert record.exists()
    assert not any(args[0] == "nvidia-smi" for args in commands)


def test_node_shares_host_cgroup_namespace_for_host_pid_quota_hooks():
    fragment = compose(SimpleNamespace(options={}))
    assert fragment["services"]["node"]["cgroup"] == "host"
