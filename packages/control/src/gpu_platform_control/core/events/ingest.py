"""Commit a module event once before acknowledging its durable node journal."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.notifications import enqueue_event
from .models import EventReceipt


@transaction.atomic
def ingest_events(node, events):
    node = ComputeNode.objects.select_for_update().get(pk=node.pk)
    declarations = {
        (module['slot'], code)
        for module in node.capabilities.get('modules', [])
        for code in module.get('events', {})
    }
    acknowledged = []
    for event in events:
        if EventReceipt.objects.filter(source="node:"+str(node.pk), event_id=event.id).exists():
            acknowledged.append(str(event.id))
            continue
        if (event.slot, event.code) not in declarations:
            raise ValidationError('module_event_unregistered', code='module_event_unregistered')
        if event.task_id:
            task = ResourceRequest.objects.filter(node=node, pk=event.task_id).select_related('user').first()
            if task is None:
                raise ValidationError('module_event_task_unknown', code='module_event_task_unknown')
            recipients = [task.user]
            target = str(task.pk)
        else:
            recipients = list(get_user_model().objects.filter(is_staff=True, is_active=True))
            target = str(node.pk)
        row = AuditLog.objects.create(
            action=event.code, target=target,
            detail={**event.parameters, '_module': {
                'node_id': node.pk, 'slot': event.slot, 'event_id': str(event.id),
                'occurred_at': event.occurred_at.isoformat(),
            }},
        )
        EventReceipt.objects.create(source="node:"+str(node.pk), event_id=event.id, audit=row)
        enqueue_event(row, recipients=recipients)
        acknowledged.append(str(event.id))
    return acknowledged


@transaction.atomic
def ingest_control_event(event, descriptor):
    source = 'control:' + descriptor.slot + ':' + descriptor.name
    if EventReceipt.objects.filter(source=source, event_id=event.id).exists():
        return
    if event.slot != descriptor.slot or event.code not in descriptor.events:
        raise ValidationError('module_event_unregistered', code='module_event_unregistered')
    if event.task_id:
        task = ResourceRequest.objects.select_related('user').get(pk=event.task_id)
        target, recipients = str(task.pk), [task.user]
    else:
        target = 'platform'
        recipients = list(get_user_model().objects.filter(is_active=True, is_staff=True))
    row = AuditLog.objects.create(action=event.code, target=target,
        detail={**event.parameters, '_module': {'source': source, 'slot': descriptor.slot,
            'module': descriptor.name, 'event_id': str(event.id), 'occurred_at': event.occurred_at.isoformat()}})
    EventReceipt.objects.create(source=source, event_id=event.id, audit=row)
    enqueue_event(row, recipients=recipients)
