from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from gpu_platform_contracts.assessment import ResourceIntent
from gpu_platform_contracts.module import ModuleContext, SlotError
from gpu_platform_contracts.resources import GPURequest
from gpu_platform_contracts.testing import GPUContract
from gpu_platform_gpu_ascend.hardware import parse_inventory
from gpu_platform_gpu_ascend.module import Module


TABLE = """
| 4 310P3 | OK | NA 41 0 / 0 |
| 0 0 | 0000:04:00.0 | 0 1283 / 44277 |
| 4 310P3 | OK | NA 39 0 / 0 |
| 1 1 | 0000:04:00.0 | 0 1655 / 43693 |
"""


@pytest.fixture
def gpu(tmp_path):
    return Module(
        ModuleContext(node_id="fixture", state_directory=tmp_path),
        read_inventory=lambda: [
            {"id": "chip-a", "index": 0, "name": "Ascend 310P3", "ready": True, "memory_gib": Decimal(40)}
        ],
    )


@pytest.fixture
def task_id():
    return "lease-fixture"


@pytest.fixture
def gpu_requests():
    return [GPURequest(device_id="chip-a", compute_percent=100, memory_gib=40)]


class TestGPUContract(GPUContract):
    pass


def test_chip_mapping_keeps_stable_identity_memory_and_health():
    rows = parse_inventory(TABLE)
    assert [r["index"] for r in rows] == [0, 1]
    assert [r["id"] for r in rows] == ["ascend:0000:04:00.0:0", "ascend:0000:04:00.0:1"]
    assert rows[0]["memory_gib"] == Decimal(44277) / 1024
    assert all(r["ready"] for r in rows)
    assert not parse_inventory(TABLE.replace("OK", "ERROR"))[0]["ready"]
    for output in ["", "invalid", TABLE + TABLE]:
        with pytest.raises(RuntimeError):
            parse_inventory(output)


def test_assessment_resolves_count_without_acquiring_a_lease(gpu):
    result = gpu.assess(ResourceIntent(resources={"npu_count": 1}))
    assert result.feasible and result.quantities == {"npu_count": 1}
    assert result.gpus[0].compute_percent == 100 and result.gpus[0].memory_gib == 40
    assert not gpu.leases()
    for count in [Decimal("0.5"), 2]:
        assert not gpu.assess(ResourceIntent(resources={"npu_count": count})).feasible


def test_whole_chip_allocation_persists_and_releases_after_restart(gpu, gpu_requests):
    lease = gpu.allocate("first", gpu_requests)
    assert lease.form == "ascend-device"
    assert lease.environment == {"ASCEND_VISIBLE_DEVICES": "0"}
    assert gpu.inventory()[0].used_compute_percent == 100
    assert not gpu.assess(ResourceIntent(resources={"npu_count": 1})).feasible
    restarted = Module(gpu.context, read_inventory=gpu.read_inventory)
    assert restarted.lookup("first") == lease
    with pytest.raises(SlotError):
        restarted.allocate("second", gpu_requests)
    restarted.release(lease.id)
    restarted.release(lease.id)
    assert gpu.lookup("first") is None
    assert gpu.assess(ResourceIntent(resources={"npu_count": 1})).feasible


def test_partial_device_requests_and_changed_retries_fail(gpu, gpu_requests):
    for values in [{"compute_percent": 50, "memory_gib": 40}, {"compute_percent": 100, "memory_gib": 8}]:
        with pytest.raises(SlotError):
            gpu.allocate("task", [GPURequest(device_id="chip-a", **values)])
    assert not gpu.leases()
    gpu.allocate("task", gpu_requests)
    with pytest.raises(SlotError):
        gpu.allocate("task", [GPURequest(device_id="chip-a", compute_percent=100, memory_gib=8)])


def test_concurrent_requests_cannot_share_the_same_physical_chip(gpu, gpu_requests):
    def allocate(task):
        try:
            return gpu.allocate(task, gpu_requests)
        except SlotError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate, ["first", "second"]))
    assert sum(r is not None for r in results) == 1
    assert gpu.resources().used["npu_count"] == 1
