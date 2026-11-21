from itertools import permutations

from gpu_platform_gpu_nvidia_mps.allocation import choose_devices


def devices():
    return [{"uuid": "device-a", "index": 0, "sm_count": 80, "usable_memory_mib": 24 * 1024},
            {"uuid": "device-b", "index": 1, "sm_count": 100, "usable_memory_mib": 40 * 1024}]


def test_allocations_fit_both_dimensions_on_distinct_devices():
    request = [{"compute_percent": 50, "memory_gib": 12}, {"compute_percent": 80, "memory_gib": 30}]
    for inventory in permutations(devices()):
        allocation = choose_devices(list(inventory), [], request)
        assert len({entry["uuid"] for entry in allocation}) == 2
        for entry in allocation:
            device = next(device for device in inventory if device["uuid"] == entry["uuid"])
            assert entry["memory_mib"] <= device["usable_memory_mib"]
            assert 0 < entry["sm_count"] <= device["sm_count"]


def test_existing_leases_reduce_allocatable_capacity():
    inventory = devices()[:1]
    allocation = choose_devices(inventory, [], [{"compute_percent": 60, "memory_gib": 20}])
    assert choose_devices(inventory, [allocation], [{"compute_percent": 50, "memory_gib": 1}]) is None
    assert choose_devices(inventory, [allocation], [{"compute_percent": 20, "memory_gib": 8}]) is None
    assert choose_devices(inventory, [allocation], [{"compute_percent": 20, "memory_gib": 2}]) is not None


def test_unavailable_devices_are_excluded():
    inventory = devices()
    for device in inventory:
        device["ready"] = False
    assert choose_devices(inventory, [], [{"compute_percent": 10, "memory_gib": 1}]) is None


def test_inventory_uses_all_driver_reported_memory(monkeypatch):
    from types import SimpleNamespace
    from gpu_platform_gpu_nvidia_mps import hardware
    def attributes(pointer, attribute, index):
        pointer._obj.value = 80
        return 0
    driver = SimpleNamespace(cuInit=lambda flags: 0, cuDeviceGetAttribute=attributes)
    monkeypatch.setattr(hardware.ctypes, "CDLL", lambda name: driver)
    monkeypatch.setattr(hardware, "run", lambda args: "GPU-a,0,fixture,24564,0000:01:00.0")
    device, = hardware.cuda_inventory()
    assert device["memory_mib"] == device["usable_memory_mib"] == 24564
    assert choose_devices([device], [], [{"compute_percent": 100, "memory_gib": 23}])
