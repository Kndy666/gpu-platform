from datetime import datetime, time, timedelta
from django.db.models import Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from .models import ResourceRequest, SystemObservation, ComputeNode

def allocation_timeline(user, day=None, node=None):
    try:
        date = datetime.strptime(day, '%Y-%m-%d').date() if day else timezone.localdate()
        if not 2 <= date.year <= 9998:
            raise ValueError('Date is outside the timezone conversion range')
    except (ValueError, TypeError):
        date = timezone.localdate()
    start = timezone.make_aware(datetime.combine(date, time.min))
    end = start + timedelta(days=1)
    now = timezone.now()
    observations = SystemObservation.objects.filter(name='gpu_inventory').select_related('node')
    if node:
        observations = observations.filter(node=node)
    devices = [{**device, 'node_id': observation.node_id} for observation in observations for device in observation.data.get('devices', [])]
    nodes = [node] if node else list(ComputeNode.objects.order_by('pk'))
    multiple = len(nodes) > 1
    def row_id(kind, node_id):
        return kind + '-' + str(node_id) if multiple else kind
    rows = []
    for item in nodes:
        inventory = sorted((device for device in devices if device['node_id'] == item.pk), key=lambda device: device['index'])
        rows.extend({'id': device['uuid'], 'node_id': item.pk, 'name': 'GPU ' + str(device['index']), 'detail': ''} for device in inventory)
        if not inventory:
            rows.append({'id': row_id('gpu', item.pk), 'node_id': item.pk, 'name': 'GPU', 'detail': '设备同步中'})
        rows.extend({'id': row_id(kind, item.pk), 'node_id': item.pk, 'name': name, 'detail': detail}
                    for kind, name, detail in [('cpu', 'CPU', '核'), ('memory', '内存', 'GiB'), ('reservations', '预约', '等待启动')])
    blocks, entries = [], []
    waiting_states = ['approved', 'scheduled', 'preparing', 'queued', 'starting']
    reservations = Q(workspace__started_at__isnull=True, mode='interactive', status__in=waiting_states)
    requests = ResourceRequest.objects.select_related('workspace', 'user').exclude(status='rejected').filter(
        Q(workspace__started_at__isnull=False) | reservations
    ).annotate(
        timeline_start=Coalesce('workspace__started_at', 'starts_at'),
        timeline_end=Coalesce('workspace__ended_at', 'ends_at', Value(min(now, end))),
    ).filter(timeline_start__lt=end, timeline_end__gt=start).defer('notes', 'command', 'argv', 'environment', 'review_reason')
    if node:
        requests = requests.filter(node=node)
    for req in requests:
        ws = getattr(req, 'workspace', None)
        if not ws: continue
        actual = ws.started_at
        booked = not actual and req.mode == 'interactive' and req.status in waiting_states
        if not actual and not booked: continue
        left = actual or req.starts_at
        right = ws.ended_at or req.ends_at or min(now, end)
        if left >= end or right <= start: continue
        mine = user.is_staff or req.user_id == user.pk
        base = {'node_id': req.node_id, 'id': str(req.pk) if mine else None, 'title': req.purpose if mine else '已分配', 'owner': req.user.username if user.is_staff else '',
                'url': '/tasks/'+str(req.pk)+'/' if mine else None,
                'left': max(0, (max(left, start)-start).total_seconds()/864), 'width': max(.3, (min(right, end)-max(left, start)).total_seconds()/864),
                'start': timezone.localtime(left).isoformat(), 'end': timezone.localtime(right).isoformat() if ws.ended_at or req.ends_at else None,
                'state': 'reserved' if booked else ('running' if not ws.ended_at else 'finished'), 'open': not ws.ended_at and req.mode == 'batch',
                'gpu': req.gpu, 'gpu_requests':req.gpu_requests,'gpu_compute_total':req.gpu_compute_total,
                'gpu_memory_total_gib':req.gpu_memory_total_gib,'cpu': req.cpu, 'memory': req.memory_gib}
        entries.append(dict(base, amount=f'{req.gpu} GPU · {req.cpu} 核'))
        if booked:
            blocks.append(dict(base, row=row_id('reservations', req.node_id), amount=f'{req.gpu} GPU · {req.cpu} 核 · {req.memory_gib} GiB'))
        else:
            for allocation in ws.gpu_allocations:
                slot=allocation['slot'];spec=req.gpu_requests[slot]
                blocks.append(dict(base,row=allocation['uuid'],gpu_slot=slot,compute_percent=spec['compute_percent'],
                    memory_gib=spec['memory_gib'],amount=f"{spec['compute_percent']}% · {spec['memory_gib']} GiB"))
            if req.gpu and not ws.gpu_devices:
                fallback = row_id('gpu', req.node_id)
                if not any(row['id'] == fallback for row in rows):
                    rows.append({'id': fallback, 'node_id': req.node_id, 'name': 'GPU', 'detail': '设备同步中'})
                blocks.append(dict(base, row=fallback, amount=str(req.gpu)+' 张'))
            blocks.append(dict(base, row=row_id('cpu', req.node_id), amount=str(req.cpu)+' 核'))
            blocks.append(dict(base, row=row_id('memory', req.node_id), amount=str(req.memory_gib)+' GiB'))
    queued = []
    if start <= now < end:
        waiting = ResourceRequest.objects.select_related('user').filter(
            workspace__isnull=False, workspace__started_at__isnull=True, workspace__ended_at__isnull=True,
            status__in=waiting_states).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(
            Q(mode='batch') | Q(mode='interactive', ends_at__gt=now)).order_by('-priority', 'created_at', 'pk')
        if node:
            waiting = waiting.filter(node=node)
        for req in waiting.defer('notes', 'command', 'argv', 'environment', 'review_reason'):
            mine = user.is_staff or req.user_id == user.pk
            queued.append({'node_id': req.node_id, 'id': str(req.pk) if mine else None,
                'title': req.purpose if mine else '排队任务', 'url': '/tasks/' + str(req.pk) + '/' if mine else None,
                'status': req.status, 'status_label': req.get_status_display(),
                'gpu': req.gpu, 'cpu': req.cpu, 'memory_gib': req.memory_gib})
    groups = [{'id': item.pk, 'name': item.name, 'gpu_count': sum(device['node_id'] == item.pk for device in devices),
               'rows': [row for row in rows if row['node_id'] == item.pk],
               'blocks': [block for block in blocks if block['node_id'] == item.pk],
               'entries': [entry for entry in entries if entry['node_id'] == item.pk],
               'queued': [entry for entry in queued if entry['node_id'] == item.pk]} for item in nodes]
    return {'date': date.isoformat(), 'rows': rows, 'blocks': blocks, 'groups': groups, 'now': (now-start).total_seconds()/864 if start <= now < end else None}
