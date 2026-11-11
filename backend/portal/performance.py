"""Host telemetry, separate from scheduling reservations and quotas."""
from datetime import timedelta
import json

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ComputeNode, SystemObservation


def record_performance(nodes):
    for node in nodes:
        raw = node['metadata'].get('annotations', {}).get('platform.performance')
        if raw:
            try:
                data = json.loads(raw)
                if data.get('version') == 1 and parse_datetime(data['sampled_at']):
                    SystemObservation.objects.update_or_create(name='performance', defaults={'data': data})
            except (ValueError, TypeError, KeyError, AttributeError):
                continue


def performance_nodes(node=None):
    observations = {item.node_id: item for item in SystemObservation.objects.filter(name='performance')}
    nodes = ComputeNode.objects.filter(pk=node.pk) if node else ComputeNode.objects.order_by('pk')
    now = timezone.now()
    result = []
    for node in nodes:
        observation = observations.get(node.pk)
        data = observation.data if observation else None
        sampled = parse_datetime(data['sampled_at']) if data else None
        fresh = bool(node.enabled and node.state != 'offline' and sampled and timezone.is_aware(sampled)
                     and now - timedelta(seconds=30) <= sampled <= now + timedelta(seconds=5))
        result.append({'id': node.pk, 'name': node.name, 'architecture': node.architecture,
            'state': node.state, 'fresh': fresh, 'sampled_at': sampled,
            'status_label': '已停用' if not node.enabled else '离线' if node.state == 'offline' else
                            '实时' if fresh else '采样过期' if sampled else '等待采样',
            'metrics': data})
    return result
