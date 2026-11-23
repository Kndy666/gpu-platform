from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.services.usage import current_usage, usage_statistics

pytestmark = pytest.mark.django_db


def test_usage_counts_audit_only_requests_and_clips_allocations_to_the_selected_day(monkeypatch):
    accounts = get_user_model().objects
    staff = accounts.create_user("administrator", is_staff=True)
    member = accounts.create_user("member")
    node = ComputeNode.objects.create(name="execution")
    image = ImageArtifact.objects.create(node=node, owner=member, name="image", reference="example/image")
    task = ResourceRequest.objects.create(user=member, node=node, image=image, mode="batch", status="succeeded")
    midnight = timezone.make_aware(datetime(2025, 1, 2))
    monkeypatch.setattr(timezone, "now", lambda: midnight + timedelta(days=3))
    Workspace.objects.create(request=task, started_at=midnight - timedelta(hours=2),
                             ended_at=midnight + timedelta(hours=2), allocations={
        "gpu": [{"device_id": "test-device", "compute_percent": 25}],
        "devices": {"test-device": {"model": "Test accelerator", "exclusive": False}},
    })
    deleted = uuid4()
    events = AuditLog.objects.bulk_create([
        AuditLog(actor=member, target=str(task.pk), action="submit"),
        AuditLog(actor=member, target=str(task.pk), action="submit"),
        AuditLog(actor=member, target=str(deleted), action="submit"),
    ])
    AuditLog.objects.filter(pk__in=[event.pk for event in events]).update(created_at=midnight - timedelta(hours=3))

    report = usage_statistics(staff).data
    assert report["totals"]["applications"] == 2
    assert report["totals"]["tasks"] == 1
    assert report["totals"]["hours"] == 4
    assert report["totals"]["gpu_hours"] == 1
    assert report["applicants"] == 1 and report["total_accounts"] == 2
    assert report["trend"]["dates"] == ["2025-01-01", "2025-01-02"]
    assert report["trend"]["series"][0]["values"] == [.5, .5]

    selection = usage_statistics(staff, query={"from": "2025-01-02", "to": "2025-01-02",
                                              "model": "Test accelerator"}).data
    assert selection["totals"]["applications"] == 0
    assert selection["totals"]["hours"] == 2
    assert selection["totals"]["gpu_hours"] == .5
    assert selection["by_model"][0]["tasks"] == 1
    with pytest.raises(PermissionDenied):
        usage_statistics(member)
    with pytest.raises(PermissionDenied):
        current_usage(member)


def test_live_usage_keeps_idle_measurements_distinct_from_missing_samples(monkeypatch):
    from gpu_platform_contracts.messages import NodeReport
    from gpu_platform_control.core.nodes.models import SystemObservation
    from gpu_platform_control.core.performance import record_performance

    staff = get_user_model().objects.create_user("administrator", is_staff=True)
    online = ComputeNode.objects.create(name="online", state="online")
    offline = ComputeNode.objects.create(name="offline", state="offline")
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)

    for node in (online, offline):
        SystemObservation.objects.create(node=node, name="gpu_inventory", data={"devices": [{
            "uuid": node.name, "index": 0, "name": "Test GPU",
            "used_compute_percent": 50, "usable_memory_mib": 4096,
        }]})
        record_performance(node, NodeReport(node_id=node.name, observed_at=now, metrics={
            "cpu.percent": 0., "cpu.cores": 8., "gpus.0.uuid": node.name,
            "gpus.0.utilization_percent": 0. if node == online else 100.,
        }))

    report = current_usage(staff).data
    assert report["online"] == 1 and report["sampled"] == 1
    assert report["capacity"]["GPU"] == {"available": 1, "allocated": .5, "percent": 50, "actual": 0}
    assert {node["name"]: node["cpu"] for node in report["nodes"]} == {"online": 0, "offline": None}
