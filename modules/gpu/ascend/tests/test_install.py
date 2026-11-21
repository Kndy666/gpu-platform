import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_platform_contracts.handoffs import GPURuntime
from gpu_platform_contracts.module import HostContext
from gpu_platform_gpu_ascend import install


def test_installer_publishes_runtime_and_preserves_operator_docker_configuration(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    config = tmp_path / "etc/base.list"
    docker = tmp_path / "etc/daemon.json"
    utility = tmp_path / "bin/npu-smi"
    docker.parent.mkdir()
    docker.write_text(json.dumps({"default-runtime": "runc", "log-driver": "local"}))
    binary = root / "ascend-docker-runtime"
    for key, value in [
        ("ROOT", root),
        ("BINARY", binary),
        ("CONFIG", config),
        ("DOCKER", docker),
        ("UTILITY", utility),
    ]:
        monkeypatch.setattr(install, key, value)
    payload = b"fixture installer"
    monkeypatch.setattr(install, "DIGEST", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(install.shutil, "which", lambda name: "/fixture/npu-smi")

    def run(args, **kwargs):
        if args[0] == "curl":
            Path(args[args.index("--output") + 1]).write_bytes(payload)
        elif args[0] == "bash":
            root.mkdir()
            binary.write_bytes(b"fixture runtime")
            config.write_text("/fixture/driver")
            docker.write_text(
                json.dumps(
                    {
                        "default-runtime": "ascend",
                        "log-driver": "local",
                        "runtimes": {"ascend": {"path": str(binary)}},
                    }
                )
            )
        elif args[0] == "install":
            utility.parent.mkdir()
            utility.write_bytes(b"fixture utility")
        return SimpleNamespace(returncode=0, stdout="")

    context = HostContext(
        node_id="worker",
        options={},
        state_directory=tmp_path / "state",
        config={},
        host={"architecture": "arm64"},
        runner=run,
    )
    install.install_host(context)
    provider = GPURuntime.model_validate(context.installation_handoffs["gpu_allocation"][0])
    assert provider.node_id == "worker" and provider.binary == str(binary) and provider.prestart_hook is None
    assert json.loads(docker.read_text())["default-runtime"] == "runc"
    install.uninstall_host(context)
    assert not binary.exists() and not utility.exists()
    assert json.loads(docker.read_text()) == {"default-runtime": "runc", "log-driver": "local"}


def test_uninstall_refuses_active_npu_leases(tmp_path):
    leases = tmp_path / "ascend/leases"
    leases.mkdir(parents=True)
    (leases / "active.json").write_text("{}")
    with pytest.raises(RuntimeError):
        install.uninstall_host(SimpleNamespace(state_directory=tmp_path))
