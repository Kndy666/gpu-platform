from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest
import gpu_platform_runtime_k3s
from gpu_platform_runtime_k3s.host.workspace_security import seccomp_profile


path = Path(gpu_platform_runtime_k3s.__file__).parent / "host" / "workspace-runtime.py"
spec = importlib.util.spec_from_file_location("workspace_runtime_probe", path)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.mark.parametrize("architecture,abi", [("amd64", "SCMP_ARCH_X86_64"), ("arm64", "SCMP_ARCH_AARCH64")])
def test_seccomp_architecture_and_keyring_restriction(architecture, abi):
    profile = seccomp_profile(architecture)
    allowed = {
        name for rule in profile["syscalls"] if rule["action"] == "SCMP_ACT_ALLOW" for name in rule["names"]
    }
    assert abi in profile["architectures"]
    assert not {"keyctl", "add_key", "request_key"} & allowed
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    if architecture == "amd64":
        assert "arch_prctl" in allowed


def test_workspace_mounts_keep_kernel_restrictions_and_add_tun():
    source = {
        "mounts": [{"destination": "/proc", "type": "proc", "source": "proc"}],
        "process": {"capabilities": {"bounding": ["CAP_SETUID", "CAP_SETGID"]}},
        "linux": {
            "maskedPaths": ["/proc/keys"],
            "readonlyPaths": ["/proc/sys"],
            "resources": {"devices": [{"allow": False, "access": "rwm"}]},
        },
    }
    before = deepcopy(source)
    result = runtime.workspace_spec(source)
    assert source == before
    assert result["process"] == source["process"]
    assert result["linux"]["maskedPaths"] == source["linux"]["maskedPaths"]
    assert result["linux"]["readonlyPaths"] == source["linux"]["readonlyPaths"]
    assert result["linux"]["resources"]["devices"] == [
        {"allow": False, "access": "rwm"},
        {"allow": True, "type": "c", "major": 10, "minor": 200, "access": "rwm"},
    ]
    mounts = {mount["destination"]: mount for mount in result["mounts"]}
    assert mounts["/run/platform-proc"]["type"] == "proc"
    assert mounts["/run/platform-sys"]["type"] == "sysfs"
    assert "ro" in mounts["/run/platform-sys"]["options"]
