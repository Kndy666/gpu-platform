import importlib.util
from types import SimpleNamespace

import pytest

from gpu_platform_runtime_k3s.install import ASSETS, queue_objects
from gpu_platform_runtime_k3s.install import registry_trust
from gpu_platform_contracts.handoffs import RegistryTrust


def load_asset(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ASSETS / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_queue_capacity_comes_from_node_allocatable_resources():
    node = {
        "metadata": {"labels": {"kubernetes.io/hostname": "worker-x"}},
        "status": {"allocatable": {"cpu": "13", "memory": "42Gi", "ephemeral-storage": "900Gi"}},
    }
    node["status"]["addresses"] = [{"type": "InternalIP", "address": "192.0.2.1"},
                                   {"type": "Hostname", "address": "worker-x"}]
    objects = queue_objects("tasks", "queue-a", node, {"service_cidr": "198.51.100.0/24"})
    policy = next(item["spec"] for item in objects if item["kind"] == "NetworkPolicy")
    assert policy["ingress"] == [{"ports": [{"port": 2222, "protocol": "TCP"}]}]
    dns, public = policy["egress"]
    assert dns["to"][0]["podSelector"]["matchLabels"]["k8s-app"] == "kube-dns"
    assert {"192.0.2.1/32", "198.51.100.0/24", "169.254.0.0/16"} <= set(public["to"][0]["ipBlock"]["except"])
    queue = next(item for item in objects if item["kind"] == "ClusterQueue")
    resources = queue["spec"]["resourceGroups"][0]["flavors"][0]["resources"]
    assert {item["name"]: item["nominalQuota"] for item in resources} == node["status"]["allocatable"]
    account = next(item for item in objects if item["kind"] == "ServiceAccount")
    assert account["automountServiceAccountToken"] is False


def test_gpu_hook_is_attached_only_to_configured_environments():
    runtime = load_asset("workspace-runtime.py")
    source = {"mounts": [], "linux": {"resources": {}}}
    cpu = runtime.workspace_spec(source)
    gpu = runtime.workspace_spec(source, "/opt/example/oci-hook")
    assert cpu.get("hooks", {}) == {}
    assert gpu["hooks"]["prestart"][0]["path"] == "/opt/example/oci-hook"
    assert source == {"mounts": [], "linux": {"resources": {}}}
    assert any(device["major"] == 10 and device["minor"] == 200 for device in cpu["linux"]["devices"])


def test_security_profile_selects_target_machine_architecture():
    security = load_asset("workspace_security.py")
    arm = security.seccomp_profile("arm64")
    amd = security.seccomp_profile("amd64")
    assert arm["architectures"] == ["SCMP_ARCH_AARCH64"]
    assert "SCMP_ARCH_X86_64" in amd["architectures"]


def test_optional_registry_and_custom_state_directory_use_provider_trust(tmp_path):
    context = SimpleNamespace(node_id="test", state_directory=tmp_path, options={}, installation_handoffs={})
    assert registry_trust(context) == {}
    certificate = tmp_path / "provider" / "authority.pem"
    certificate.parent.mkdir()
    certificate.write_bytes(b"fixture-public-ca")
    context.installation_handoffs["image"] = [
        RegistryTrust(node_id="test", endpoint="registry.example:5000", ca_file=certificate).model_dump(
            mode="json"
        )
    ]
    assert registry_trust(context) == {"registry.example:5000": certificate}
    context.options["registry_trust"] = {"external.example": "provider/authority.pem"}
    assert registry_trust(context)["external.example"] == certificate
    context.options["registry_trust"]["external.example"] = "missing.pem"
    with pytest.raises(FileNotFoundError):
        registry_trust(context)


def test_uninstall_removes_only_created_cluster_objects(tmp_path):
    from gpu_platform_runtime_k3s.install import own_object, uninstall
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout="runtimeclass/existing" if "existing" in args else "", returncode=0)

    context = SimpleNamespace(state_directory=tmp_path, runner=run)
    own_object(context, "RuntimeClass", "existing")
    own_object(context, "RuntimeClass", "created")
    own_object(context, "PersistentVolumeClaim", "resource-view", "workloads")
    uninstall(context)
    deleted = [args[3:5] for args in commands if args[2] == "delete"]
    assert deleted == [["PersistentVolumeClaim", "resource-view"], ["RuntimeClass", "created"]]
    uninstall(context)
    assert len([args for args in commands if args[2] == "delete"]) == 2


def test_helm_install_and_owned_uninstall_use_k3s_config_independent_of_caller(tmp_path, monkeypatch):
    from gpu_platform_runtime_k3s.install import install_queue, uninstall

    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "unrelated-cluster.yaml"))
    monkeypatch.setenv("HOME", str(tmp_path / "no-default-kubeconfig"))
    commands = []

    def runner(args, **kwargs):
        assert args[:3] == ["helm", "--kubeconfig", "/etc/rancher/k3s/k3s.yaml"]
        commands.append(args)
        return SimpleNamespace(stdout="[]", returncode=0)

    context = SimpleNamespace(state_directory=tmp_path, runner=runner)
    install_queue(context, tmp_path / "kueue.tgz", tmp_path / "values.yaml")
    uninstall(context)
    assert [args[3] for args in commands] == ["list", "upgrade", "uninstall"]


def test_workspace_runtime_uses_the_provider_executable():
    runtime = load_asset("workspace-runtime.py")
    text = runtime.runtime_template({"cpu":"workspace", "gpu":"accelerated"}, "/vendor/accelerator-runtime")
    assert 'BinaryName = "/vendor/accelerator-runtime"' in text
    assert 'BinaryName = "/usr/bin/runc"' in text
    with pytest.raises(ValueError):
        runtime.runtime_template({"gpu":"accelerated"})


@pytest.mark.parametrize('runc_installed', [True, False])
def test_host_setup_reuses_an_existing_oci_runtime(tmp_path, monkeypatch, runc_installed):
    from types import SimpleNamespace
    from gpu_platform_runtime_k3s import install

    class ClusterWait(Exception):
        pass

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ['k3s', 'kubectl', 'wait']:
            raise ClusterWait

    monkeypatch.setattr(install.shutil, 'which',
        lambda name: None if name == 'runc' and not runc_installed else '/fixture/' + name)
    context = SimpleNamespace(state_directory=tmp_path, host={'architecture': 'arm64'}, runner=run)
    with pytest.raises(ClusterWait):
        install._install_host(context)
    installed = [package for call in calls if call[:2] == ['apt-get', 'install'] for package in call[2:]]
    assert ('runc' in installed) is not runc_installed
    assert 'fuse' not in installed


def test_initial_cluster_readiness_waits_for_node_registration(monkeypatch):
    from gpu_platform_runtime_k3s import install
    results = iter([SimpleNamespace(returncode=1, stderr='no nodes registered'),
                    SimpleNamespace(returncode=0, stderr='')])
    sleeps = []
    monkeypatch.setattr(install.time, 'sleep', sleeps.append)
    install.wait_for_node(SimpleNamespace(runner=lambda *args, **kwargs: next(results)))
    assert sleeps == [1]


def test_initial_cluster_readiness_has_a_deadline(monkeypatch):
    from gpu_platform_runtime_k3s import install
    ticks = iter([0, 181])
    monkeypatch.setattr(install.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError):
        install.wait_for_node(SimpleNamespace(runner=lambda *args, **kwargs:
            SimpleNamespace(returncode=1, stderr='unavailable')))
