from datetime import datetime, time, timedelta
from django.db.models import Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from .models import ResourceRequest, SystemObservation

def allocation_timeline(user, day=None):
    try:
        date = datetime.strptime(day, '%Y-%m-%d').date() if day else timezone.localdate()
        if not 2 <= date.year <= 9998:
            raise ValueError('Date is outside the timezone conversion range')
    except (ValueError, TypeError):
        date = timezone.localdate()
    start = timezone.make_aware(datetime.combine(date, time.min))
    end = start + timedelta(days=1)
    now = timezone.now()
    obs = SystemObservation.objects.filter(name='gpu_inventory').first()
    devices = obs.data.get('devices', []) if obs else []
    rows = [{'id': d['uuid'], 'name': 'GPU '+str(d['index']), 'detail': d['name']} for d in devices]
    rows += [{'id': 'gpu', 'name': 'GPU', 'detail': '设备信息同步中'}] if not devices else []
    rows += [{'id': 'cpu', 'name': 'CPU', 'detail': '核'}, {'id': 'memory', 'name': '内存', 'detail': 'GiB'}]
    blocks = []
    reservations = Q(workspace__started_at__isnull=True, mode='interactive', status__in=['approved', 'scheduled', 'queued', 'starting'])
    requests = ResourceRequest.objects.select_related('workspace', 'user').exclude(status='rejected').filter(
        Q(workspace__started_at__isnull=False) | reservations
    ).annotate(
        timeline_start=Coalesce('workspace__started_at', 'starts_at'),
        timeline_end=Coalesce('workspace__ended_at', 'ends_at', Value(min(now, end))),
    ).filter(timeline_start__lt=end, timeline_end__gt=start).defer('notes', 'command', 'argv', 'environment', 'review_reason')
    for req in requests:
        ws = getattr(req, 'workspace', None)
        if not ws: continue
        actual = ws.started_at
        booked = not actual and req.mode == 'interactive' and req.status in {'approved', 'scheduled', 'queued', 'starting'}
        if not actual and not booked: continue
        left = actual or req.starts_at
        right = ws.ended_at or req.ends_at or min(now, end)
        if left >= end or right <= start: continue
        mine = user.is_staff or req.user_id == user.pk
        base = {'id': str(req.pk) if mine else None, 'title': req.purpose if mine else '已分配', 'owner': req.user.username if user.is_staff else '',
                'url': '/tasks/'+str(req.pk)+'/' if mine else None,
                'left': max(0, (max(left, start)-start).total_seconds()/864), 'width': max(.3, (min(right, end)-max(left, start)).total_seconds()/864),
                'start': timezone.localtime(left).isoformat(), 'end': timezone.localtime(right).isoformat() if ws.ended_at or req.ends_at else None,
                'state': 'reserved' if booked else ('running' if not ws.ended_at else 'finished'), 'open': not ws.ended_at and req.mode == 'batch',
                'gpu': req.gpu, 'gpu_requests':req.gpu_requests,'gpu_compute_total':req.gpu_compute_total,
                'gpu_memory_total_gib':req.gpu_memory_total_gib,'cpu': req.cpu, 'memory': req.memory_gib}
        if booked:
            blocks.append(dict(base, row='reservations', amount=f'{req.gpu} GPU · {req.cpu} 核 · {req.memory_gib} GiB'))
        else:
            for allocation in ws.gpu_allocations:
                slot=allocation['slot'];spec=req.gpu_requests[slot]
                blocks.append(dict(base,row=allocation['uuid'],gpu_slot=slot,compute_percent=spec['compute_percent'],
                    memory_gib=spec['memory_gib'],amount=f"{spec['compute_percent']}% · {spec['memory_gib']} GiB"))
            if req.gpu and not ws.gpu_devices:
                if not any(r['id'] == 'gpu' for r in rows): rows.insert(len(devices), {'id': 'gpu', 'name': 'GPU', 'detail': '设备同步中'})
                blocks.append(dict(base, row='gpu', amount=str(req.gpu)+' 张'))
            blocks.append(dict(base, row='cpu', amount=str(req.cpu)+' 核'))
            blocks.append(dict(base, row='memory', amount=str(req.memory_gib)+' GiB'))
    rows.append({'id': 'reservations', 'name': '预约', 'detail': '等待启动'})
    return {'date': date.isoformat(), 'rows': rows, 'blocks': blocks, 'now': (now-start).total_seconds()/864 if start <= now < end else None}
