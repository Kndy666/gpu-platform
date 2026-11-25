import pytest

from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.resources import GPURequest
from gpu_platform_contracts.assessment import ResourceIntent
from gpu_platform_contracts.testing import GPUContract
from gpu_platform_gpu_nvidia_mps.module import Module


@pytest.fixture
def gpu(tmp_path, driver):
    devices = [
        {"uuid": "device", "name": "Accelerator", "index": 0, "sm_count": 80, "usable_memory_mib": 8192}
    ]

    return Module(ModuleContext(state_directory=tmp_path), inventory=lambda: devices, run=driver)


@pytest.fixture
def task_id():
    return "lease-fixture"


@pytest.fixture
def gpu_requests():
    return [GPURequest(device_id="device", compute_percent=50, memory_gib=4)]


class TestGPUContract(GPUContract):
    pass


def test_maximum_resolves_at_module_boundary_even_when_hardware_is_busy(gpu):
    gpu.allocate("occupied", [GPURequest(device_id="device", compute_percent=100, memory_gib=8)])
    before = gpu.leases()
    intent = ResourceIntent(
        allocation_requests={"gpu": [{"gpu_compute": "max", "gpu_memory": "max"}]},
        limits={"gpu_compute": 50, "gpu_memory": 4},
        per_allocation_limits={"gpu_compute": 50, "gpu_memory": 4},
    )
    result = gpu.assess(intent)
    assert not result.feasible
    assert result.allocations == {"gpu": [{"gpu_compute": 50, "gpu_memory": 4}]}
    assert result.gpus[0].device_id == "auto:0"
    assert gpu.leases() == before


def test_maximum_respects_zero_policy_capacity(gpu):
    result = gpu.assess(
        ResourceIntent(
            allocation_requests={"gpu": [{"gpu_compute": "max", "gpu_memory": "max"}]},
            per_allocation_limits={"gpu_compute": 0, "gpu_memory": 4},
        )
    )
    assert not result.feasible and not result.allocations
