from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.resources import GPURequest
from gpu_platform_gpu_nvidia_mps.module import Module


def inventory():
    return [{"uuid": "device-a", "name": "Accelerator A", "index": 0, "sm_count": 80, "usable_memory_mib": 24 * 1024},
            {"uuid": "device-b", "name": "Accelerator B", "index": 1, "sm_count": 100, "usable_memory_mib": 40 * 1024}]


def test_explicit_device_selection_and_lease_survive_restart(tmp_path, driver):
    context = ModuleContext(node_id="node-a", state_directory=tmp_path)
    module = Module(context, inventory=inventory, run=driver)
    request = [GPURequest(device_id="device-b", compute_percent=40, memory_gib=20)]
    allocated = module.allocate("task-a", request)
    assert allocated.devices[0].device_id == "device-b"
    restarted = Module(context, inventory=inventory, run=driver)
    assert restarted.allocate("task-a", request) == allocated
    assert restarted.resources().used == {"gpu_compute": 40, "gpu_memory": 20}
    restarted.release(allocated.id)
    assert restarted.resources().used == {"gpu_compute": 0, "gpu_memory": 0}
    restarted.release(allocated.id)


def test_handoff_mounts_only_the_allocated_private_service(tmp_path, driver):
    from pathlib import Path

    context = ModuleContext(state_directory=tmp_path)
    module = Module(context, inventory=inventory, run=driver)
    request = [GPURequest(device_id="device-a", compute_percent=50, memory_gib=2)]
    first = module.allocate("task-a", request)
    second = module.allocate("task-b", request)
    assert first.worker and second.worker
    assert first.environment["NVIDIA_VISIBLE_DEVICES"] == "void" and not first.mounts
    route = Path(first.worker.mounts[0]["source"])
    assert (route / "control").is_socket() and (route / "log").is_fifo()
    assert first.worker.mounts[0]["target"] == first.worker.environment["CUDA_MPS_PIPE_DIRECTORY"]
    assert first.worker.mounts[0] != second.worker.mounts[0]
    restarted = Module(context, inventory=inventory, run=driver)
    assert restarted.lookup("task-a") == first
    generation = Path(first.worker.mounts[1]["source"]) / "ready"
    neighbor = Path(second.worker.mounts[1]["source"]) / "ready"
    original, other = generation.read_text(), neighbor.read_text()
    (Path(first.worker.mounts[2]["source"]) / original).touch()
    restarted.reconcile("task-a")
    assert generation.read_text() != original and neighbor.read_text() == other
    restarted.reconcile("task-a", stopping=True)
    assert generation.read_text() == "closed" and neighbor.read_text() == other
    assert len(driver.servers) == 1
    assert restarted.resources().used == {"gpu_compute": 100, "gpu_memory": 4}
    restarted.release(first.id)
    assert not route.exists() and restarted.lookup("task-b") == second
    assert len(driver.servers) == 1
    restarted.release(second.id)
    assert not driver.servers and not driver.partitions and not restarted.leases()


def test_interrupted_preparation_rolls_back_private_server(tmp_path, driver):
    import pytest
    driver.fail = ("server", "set")
    module = Module(ModuleContext(state_directory=tmp_path), inventory=inventory, run=driver)
    with pytest.raises(RuntimeError):
        module.allocate("task-a", [GPURequest(device_id="device-a", compute_percent=30, memory_gib=2)])
    assert not driver.partitions and not driver.servers and not module.leases()


def test_failed_release_keeps_durable_reservation_until_retry(tmp_path, driver):
    import pytest
    context = ModuleContext(state_directory=tmp_path)
    module = Module(context, inventory=inventory, run=driver)
    allocation = module.allocate("task-a", [GPURequest(device_id="device-a", compute_percent=30, memory_gib=2)])
    driver.fail = ("server", "delete")
    with pytest.raises(RuntimeError):
        module.release(allocation.id)
    assert module.resources().used["gpu_compute"] == 30
    driver.fail = None
    Module(context, inventory=inventory, run=driver).release(allocation.id)
    assert not module.leases() and not driver.servers and not driver.partitions


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


def test_quota_hook_uses_absolute_host_cgroup_and_rejects_private_namespace_traversal(tmp_path, monkeypatch, driver):
    import json
    from pathlib import Path
    import pytest

    module = Module(ModuleContext(state_directory=tmp_path), inventory=inventory, run=driver)
    lease = module.allocate("task-a", [GPURequest(device_id="device-a", compute_percent=30, memory_gib=2)])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "config.json").write_text(json.dumps({"process": {"env": [f"{key}={value}" for key, value in lease.worker.environment.items()]}}))
    cgroup = "0::/../../kubepods.slice/pod-task.scope\n"
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        return cgroup if str(path) == "/proc/4321/cgroup" else original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(PermissionError):
        module.quota_hook({"bundle": str(bundle), "pid": 4321})
    assert not driver.quotas
    cgroup = "0::/kubepods.slice/pod-task.scope\n"
    module.quota_hook({"bundle": str(bundle), "pid": 4321})
    assert driver.quotas[0][driver.quotas[0].index("-n") + 1] == "/sys/fs/cgroup/kubepods.slice/pod-task.scope"
    assert module.leases()[0]["cgroup"] == "/sys/fs/cgroup/kubepods.slice/pod-task.scope"
