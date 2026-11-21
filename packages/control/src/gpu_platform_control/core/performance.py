"""Host telemetry, separate from scheduling reservations and quotas."""

from django.utils.translation import gettext_lazy as tr
from datetime import timedelta
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from gpu_platform_control.core.nodes.capabilities import accelerator_texts
from gpu_platform_control.core.nodes.models import ComputeNode, SystemObservation


def record_performance(node, report):
    if not report.metrics:
        return
    nested: dict = {}
    for key, value in report.metrics.items():
        cursor = nested
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value

    def arrays(value):
        if not isinstance(value, dict):
            return value
        if value and all((key.isdigit() for key in value)):
            return [arrays(value[key]) for key in sorted(value, key=int)]
        return {key: arrays(child) for (key, child) in value.items()}

    data = arrays(nested)
    data["sampled_at"] = report.observed_at.isoformat()
    data["series"] = report.metrics
    SystemObservation.objects.update_or_create(node=node, name="performance", defaults={"data": data})


def performance_nodes(node=None):
    observations = {item.node_id: item for item in SystemObservation.objects.filter(name="performance")}
    nodes = ComputeNode.objects.filter(pk=node.pk) if node else ComputeNode.objects.order_by("pk")
    now = timezone.now()
    result = []
    for node in nodes:
        observation = observations.get(node.pk)
        data = observation.data if observation else None
        sampled = parse_datetime(data["sampled_at"]) if data else None
        fresh = bool(
            node.enabled
            and node.state != "offline"
            and sampled
            and timezone.is_aware(sampled)
            and (now - timedelta(seconds=30) <= sampled <= now + timedelta(seconds=5))
        )
        result.append(
            {
                "id": node.pk,
                "name": node.name, **accelerator_texts(node),
                "architecture": node.architecture,
                "state": node.state,
                "fresh": fresh,
                "sampled_at": sampled,
                "status_label": tr("Disabled")
                if not node.enabled
                else tr("Offline")
                if node.state == "offline"
                else tr("Live")
                if fresh
                else tr("Metrics stale")
                if sampled
                else tr("Waiting for metrics"),
                "metrics": data,
            }
        )
    return result
