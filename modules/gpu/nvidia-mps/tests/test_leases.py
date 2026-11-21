from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.resources import GPURequest
from gpu_platform_gpu_nvidia_mps.module import Module


def inventory():
    return [{"uuid": "device-a", "name": "Accelerator A", "index": 0, "sm_count": 80, "usable_memory_mib": 24 * 1024},
            {"uuid": "device-b", "name": "Accelerator B", "index": 1, "sm_count": 100, "usable_memory_mib": 40 * 1024}]


def runner(args, **kwargs):
    if args[3:5] == ["server", "list"]:
        return "shared,Ready\n"
    return ""


def test_explicit_device_selection_and_lease_survive_restart(tmp_path):
    context = ModuleContext(node_id="node-a", state_directory=tmp_path)
    module = Module(context, inventory=inventory, run=runner)
    request = [GPURequest(device_id="device-b", compute_percent=40, memory_gib=20)]
    allocated = module.allocate("task-a", request)
    assert allocated.devices[0].device_id == "device-b"
    restarted = Module(context, inventory=inventory, run=runner)
    assert restarted.allocate("task-a", request) == allocated
    assert restarted.resources().used == {"gpu_compute": 40, "gpu_memory": 20}
    restarted.release(allocated.id)
    assert restarted.resources().used == {"gpu_compute": 0, "gpu_memory": 0}
    restarted.release(allocated.id)


class Driver:
    def __init__(self, pipes=None):
        self.namespaces = set()
        self.partitions = set()
        self.fail = None
        self.pipes = pipes

    def __call__(self, args, **kwargs):
        command = args[3:]
        if self.fail == tuple(command[:2]):
            raise RuntimeError("injected")
        if command[:2] == ["server", "list"]:
            return "shared,Ready\n"
        collection = self.namespaces if command[0] == "namespace" else self.partitions
        if command[1] == "list":
            return "\n".join(sorted(collection))
        if command[1] == "create":
            collection.add(command[2])
            if command[0] == "namespace" and self.pipes is not None:
                server = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--server="))
                (self.pipes / server / command[2]).mkdir(parents=True)
        elif command[1] == "delete":
            collection.discard(command[2])
            if command[0] == "namespace" and self.pipes is not None:
                server = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--server="))
                (self.pipes / server / command[2]).rmdir()
        return ""


def test_handoff_mounts_created_server_namespace_directory_and_release_removes_it(tmp_path):
    from pathlib import Path

    pipes = tmp_path / "gpu" / "pipes"
    driver = Driver(pipes)
    context = ModuleContext(state_directory=tmp_path)
    module = Module(context, inventory=inventory, run=driver)
    lease = module.allocate("task-a", [GPURequest(device_id="device-a", compute_percent=30, memory_gib=2)])
    mount = lease.mounts[0]
    namespace = Path(mount["source"])
    assert namespace.is_dir()
    assert namespace.parent == pipes / "shared"
    assert mount["target"] == lease.environment["CUDA_MPS_PIPE_DIRECTORY"]
    assert mount["read_only"] is True
    restarted = Module(context, inventory=inventory, run=driver)
    assert restarted.lookup("task-a").mounts == lease.mounts
    restarted.release(lease.id)
    assert not namespace.exists() and not driver.namespaces and not restarted.leases()


def test_interrupted_preparation_rolls_back_partitions_and_namespace(tmp_path):
    import pytest
    driver = Driver()
    driver.fail = ("namespace", "set")
    module = Module(ModuleContext(state_directory=tmp_path), inventory=inventory, run=driver)
    with pytest.raises(RuntimeError):
        module.allocate("task-a", [GPURequest(device_id="device-a", compute_percent=30, memory_gib=2)])
    assert not driver.partitions and not driver.namespaces and not module.leases()


def test_failed_release_keeps_durable_reservation_until_retry(tmp_path):
    import pytest
    driver = Driver()
    context = ModuleContext(state_directory=tmp_path)
    module = Module(context, inventory=inventory, run=driver)
    allocation = module.allocate("task-a", [GPURequest(device_id="device-a", compute_percent=30, memory_gib=2)])
    driver.fail = ("namespace", "delete")
    with pytest.raises(RuntimeError):
        module.release(allocation.id)
    assert module.resources().used["gpu_compute"] == 30
    driver.fail = None
    Module(context, inventory=inventory, run=driver).release(allocation.id)
    assert not module.leases() and not driver.namespaces and not driver.partitions


def test_assessment_selects_devices_without_creating_leases_or_driver_state(tmp_path):
    from gpu_platform_contracts.assessment import ResourceIntent
    def no_driver_calls(*args, **kwargs):
        raise AssertionError("resource assessment executed a driver mutation")
    module = Module(ModuleContext(state_directory=tmp_path), inventory=inventory, run=no_driver_calls)
    intent = ResourceIntent(gpus=[GPURequest(device_id="auto:0", compute_percent=25, memory_gib=30)])
    result = module.assess(intent)
    assert result.feasible and result.gpus[0].device_id == "device-b"
    assert result.quantities == {"gpu_compute": 25, "gpu_memory": 30}
    assert not module.leases()


def test_quota_hook_uses_absolute_host_cgroup_and_rejects_private_namespace_traversal(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import pytest

    driver = Driver()
    quotas = []

    def run(args, **kwargs):
        if args[0] == "nvidia-smi":
            quotas.append(args)
            return ""
        return driver(args, **kwargs)

    module = Module(ModuleContext(state_directory=tmp_path), inventory=inventory, run=run)
    lease = module.allocate("task-a", [GPURequest(device_id="device-a", compute_percent=30, memory_gib=2)])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "config.json").write_text(json.dumps({"process": {"env": [f"{key}={value}" for key, value in lease.environment.items()]}}))
    cgroup = "0::/../../kubepods.slice/pod-task.scope\n"
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        return cgroup if str(path) == "/proc/4321/cgroup" else original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(PermissionError):
        module.quota_hook({"bundle": str(bundle), "pid": 4321})
    assert not quotas
    cgroup = "0::/kubepods.slice/pod-task.scope\n"
    module.quota_hook({"bundle": str(bundle), "pid": 4321})
    assert quotas[0][quotas[0].index("-n") + 1] == "/sys/fs/cgroup/kubepods.slice/pod-task.scope"
    assert module.leases()[0]["cgroup"] == "/sys/fs/cgroup/kubepods.slice/pod-task.scope"
