"""Resource timelines projected from registered quantities and task intervals."""

from datetime import datetime, time, timedelta
from django.db.models import Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, get_language
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.nodes.models import ComputeNode, SystemObservation
from gpu_platform_control.core.quotas.models import NodeResource
from .projections import task_fields
from gpu_platform_control.core.nodes.capabilities import accelerator_module, accelerator_texts


def device_fields(request):
    fields = task_fields(request)
    module = accelerator_module(request.node)
    fields['gpu'] = fields['gpu'] or sum(int(request.resource_quantities.get(row['key'], 0))
        for row in module.get('resources', []) if row['unit'] == 'count')
    fields['device_label'] = accelerator_texts(request.node)['device_label']
    if not fields['gpu_requests'] and request.workspace.allocations.get('gpu'):
        fields['gpu_requests'] = request.workspace.allocations['gpu']
    return fields


def compact(value):
    return f'{float(value):.2f}'.rstrip('0').rstrip('.')


def allocation_timeline(user, day=None, node=None):
    try:
        date = datetime.strptime(day, '%Y-%m-%d').date() if day else timezone.localdate()
        if not 2 <= date.year <= 9998:
            raise ValueError
    except (ValueError, TypeError):
        date = timezone.localdate()
    start = timezone.make_aware(datetime.combine(date, time.min))
    end = start + timedelta(days=1)
    now = timezone.now()
    nodes = [node] if node else list(ComputeNode.objects.order_by('pk'))
    definitions = {item.pk: {row.resource_id: row.resource for row in NodeResource.objects.filter(node=item).select_related('resource')} for item in nodes}
    inventories = {item.node_id: item.data.get("devices", [])
                   for item in SystemObservation.objects.filter(name="gpu_inventory", node__in=nodes)}
    rows = []
    for item in nodes:
        for device in sorted(inventories.get(item.pk, []), key=lambda row: row["index"]):
            rows.append({"id": device["uuid"], "node_id": item.pk, "name": accelerator_texts(item)["device_label"] + " " + str(device["index"]), "detail": ""})
        for key, definition in definitions[item.pk].items():
            if definition.accounting != "task" or definition.allocation_group:
                continue
            rows.append({"id": f"{item.pk}:{key}", "node_id": item.pk,
                         "name": LocalizedText.model_validate(definition.label).resolve(get_language() or "en"),
                         "detail": definition.unit})
        rows.append({"id": f"reservations-{item.pk}", "node_id": item.pk, "name": _("Scheduled"), "detail": ""})
    waiting = ['approved', 'scheduled', 'preparing', 'queued', 'starting']
    requests = ResourceRequest.objects.select_related('workspace', 'user').filter(node__in=nodes).exclude(status='rejected').filter(
        Q(workspace__started_at__isnull=False) | Q(workspace__started_at__isnull=True, mode='interactive', status__in=waiting)
    ).annotate(timeline_start=Coalesce('workspace__started_at', 'starts_at'),
               timeline_end=Coalesce('workspace__ended_at', 'ends_at', Value(min(now, end)))).filter(timeline_start__lt=end, timeline_end__gt=start)
    blocks, entries = [], []
    for request in requests:
        workspace = request.workspace
        booked = workspace.started_at is None
        left = workspace.started_at or request.starts_at
        right = workspace.ended_at or request.ends_at or min(now, end)
        mine = user.is_staff or request.user_id == user.pk
        fields = device_fields(request)
        base = {'node_id': request.node_id, 'id': str(request.pk) if mine else None,
            'title': request.purpose if mine else _('Allocated'), 'owner': request.user.username if user.is_staff else '',
            'url': '/tasks/'+str(request.pk)+'/' if mine else None,
            'left': max(0, (max(left, start)-start).total_seconds()/864),
            'width': max(.3, (min(right, end)-max(left, start)).total_seconds()/864),
            'start': timezone.localtime(left).isoformat(),
            'end': timezone.localtime(right).isoformat() if workspace.ended_at or request.ends_at else None,
            'state': 'reserved' if booked else 'running' if not workspace.ended_at else 'finished',
            'open': not workspace.ended_at and request.mode == 'batch', **fields,
            'memory': fields['memory_gib'], 'extensions': {'resources': request.resource_quantities}}
        amounts = [f"{amount} {definitions[request.node_id][key].unit}" for key, amount in request.resource_quantities.items()
                   if key in definitions[request.node_id] and definitions[request.node_id][key].accounting == "task"
                   and not definitions[request.node_id][key].allocation_group]
        entries.append({**base, "amount": "  ".join(amounts)})
        if booked:
            blocks.append({**base, "row": f"reservations-{request.node_id}", "amount": "  ".join(amounts)})
        else:
            for index, allocation in enumerate(workspace.allocations.get("gpu", [])):
                spec = fields["gpu_requests"][index] if index < len(fields["gpu_requests"]) else allocation
                device = allocation.get("device_id")
                if device and not any(row["id"] == device for row in rows):
                    rows.append({"id": device, "node_id": request.node_id, "name": fields['device_label'], "detail": ""})
                if device:
                    blocks.append({**base, "row": device, "gpu_slot": index,
                                   "amount": f'{compact(spec.get("compute_percent", 0))}%  {compact(spec.get("memory_gib", 0))} GiB'})
            for key, amount in request.resource_quantities.items():
                definition = definitions[request.node_id].get(key)
                if definition and definition.accounting == "task" and not definition.allocation_group:
                    blocks.append({**base, "row": f"{request.node_id}:{key}", "amount": f"{amount} {definition.unit}"})
    queued = []
    if start <= now < end:
        pending = ResourceRequest.objects.filter(node__in=nodes, workspace__isnull=False, workspace__started_at__isnull=True,
            workspace__ended_at__isnull=True, status__in=waiting).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(
                Q(mode='batch') | Q(mode='interactive', ends_at__gt=now)).order_by('-priority', 'created_at')
        for request in pending:
            mine = user.is_staff or request.user_id == user.pk
            queued.append({'node_id': request.node_id, 'id': str(request.pk) if mine else None,
                'title': request.purpose if mine else _('Queued tasks'), 'url': '/tasks/'+str(request.pk)+'/' if mine else None,
                'status': request.status, 'status_label': request.get_status_display(), **device_fields(request)})
    return {'date': date.isoformat(), 'rows': rows, 'blocks': blocks,
        'groups': [{'id': item.pk, 'name': item.name, 'gpu_count': len(item.capabilities.get('gpus', [])),
                    'rows': [row for row in rows if row['node_id'] == item.pk],
                    'blocks': [row for row in blocks if row['node_id'] == item.pk],
                    'entries': [row for row in entries if row['node_id'] == item.pk],
                    'queued': [row for row in queued if row['node_id'] == item.pk]} for item in nodes],
        'now': (now-start).total_seconds()/864 if start <= now < end else None}
