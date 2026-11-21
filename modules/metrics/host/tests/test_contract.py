from types import SimpleNamespace

import pytest

from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.testing import MetricsContract
from gpu_platform_metrics_host.module import Module


@pytest.fixture
def metrics(tmp_path):
    sampler = SimpleNamespace(sample=lambda: {"cpu": {"usage_percent": 25.0, "ready": True},
        "memory": {"used_gib": 3}, "gpus": [{"temperature": None, "usage": 0}]})
    return Module(ModuleContext(node_id="node", state_directory=tmp_path), sampler=sampler)


class TestHostMetrics(MetricsContract):
    pass


def test_numeric_measurements_keep_zero_and_exclude_status_flags(metrics):
    assert metrics.sample() == {"cpu.usage_percent": 25.0, "memory.used_gib": 3.0, "gpus.0.usage": 0.0, "gpus.0.temperature": None}



def test_device_labels_survive_typed_node_report(metrics):
    from datetime import datetime, timezone
    from gpu_platform_contracts.messages import NodeReport
    metrics.sampler.sample = lambda: {"cpu": {"percent": None}, "disks": [{"name": "data-1", "total_gib": 2048}],
                                     "network": [{"name": "enp1s0.100", "rx_mib_s": 0}]}
    report = NodeReport(node_id="fixture", observed_at=datetime.now(timezone.utc), metrics=metrics.sample())
    decoded = NodeReport.model_validate_json(report.model_dump_json())
    assert decoded.metrics["disks.0.name"] == "data-1"
    assert decoded.metrics["network.0.name"] == "enp1s0.100"
    assert decoded.metrics["cpu.percent"] is None and decoded.metrics["network.0.rx_mib_s"] == 0
