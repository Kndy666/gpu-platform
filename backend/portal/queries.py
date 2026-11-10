"""Portal read models consumed by both HTML views and the JSON API."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone
from .access import images_for
from .models import AuditLog, ComputeNode, ImageArtifact, ResourceRequest, StoragePool, SystemObservation, UserGroup
from .services import ACTIVE, pool_available


def resource_overview(node=None):
    observations = SystemObservation.objects.filter(name='resources', node__enabled=True, observed_at__gte=timezone.now()-timedelta(minutes=2)).exclude(node__state='offline')
    if node:
        observations = observations.filter(node=node)
    observations = list(observations)
    fresh = bool(observations)
    combined = {}
    for observation in observations:
        for name, values in observation.data.items():
            if isinstance(values, dict) and 'total' in values:
                result = combined.setdefault(name, {'total': 0, 'used': 0, 'available': 0})
                for key in ('total', 'used', 'available'):
                    result[key] += values.get(key, 0)
    for values in combined.values():
        values['used_percent'] = round(min(100, values['used'] / values['total'] * 100), 1) if values['total'] else 0
    pools = StoragePool.objects.filter(healthy=True, node__enabled=True, observed_at__gte=timezone.now()-timedelta(minutes=2)).exclude(node__state='offline')
    if node:
        pools = pools.filter(node=node)
    available = sum(pool_available(pool) for pool in pools)
    counts = tasks_for_counts(node)
    inventories=SystemObservation.objects.filter(name='gpu_inventory').select_related('node')
    if node:
        inventories = inventories.filter(node=node)
    devices=[{**device, 'node_id': inventory.node_id, 'node_name': inventory.node.name,
              'ready': device.get('ready', True) and inventory.node.enabled and inventory.node.state != 'offline'
                       and inventory.observed_at >= timezone.now()-timedelta(minutes=2)}
             for inventory in inventories for device in inventory.data.get('devices',[])]
    for d in devices:
        d['available_compute_percent']=max(0,100-d.get('used_compute_percent',0)) if d.get('ready',True) else 0
        d['available_memory_gib']=round(max(0,d.get('usable_memory_mib',d.get('memory_mib',0))-d.get('used_memory_mib',0))/1024,1) if d['ready'] else 0
        d['used_memory_gib']=round(d.get('used_memory_mib',0)/1024,1)
    return {'resources': combined if fresh else None, 'gpus':devices, 'fresh': fresh,
            'available_storage_gib': available, 'storage_tib': round(available/1024, 1), **counts}


def tasks_for_counts(node=None):
    from .models import ResourceRequest
    query = ResourceRequest.objects.filter(node=node) if node else ResourceRequest.objects.all()
    counts = query.aggregate(
        running_count=Count('pk', filter=Q(status='running')),
        queued_count=Count('pk', filter=Q(status__in=ACTIVE-{'running'})))
    return counts


def node_resource_groups(overview):
    cutoff = timezone.now() - timedelta(minutes=2)
    observations = {row.node_id: row for row in SystemObservation.objects.filter(name='resources')}
    storage = {}
    for pool in StoragePool.objects.filter(healthy=True, observed_at__gte=cutoff).select_related('node'):
        storage[pool.node_id] = storage.get(pool.node_id, 0) + pool_available(pool)
    groups = []
    for node in ComputeNode.objects.order_by('pk'):
        observation = observations.get(node.pk)
        fresh = bool(node.enabled and node.state != 'offline' and observation and observation.observed_at >= cutoff)
        devices = sorted(({**gpu, 'memory_total_gib': round(gpu.get('usable_memory_mib', gpu.get('memory_mib', 0)) / 1024, 1)}
                          for gpu in overview['gpus'] if gpu['node_id'] == node.pk), key=lambda gpu: gpu['index'])
        models = list(dict.fromkeys(gpu['name'].removeprefix('NVIDIA ').removeprefix('GeForce ') for gpu in devices))
        state = 'maintenance' if node.maintenance else 'disabled' if not node.enabled else 'online' if fresh else 'offline'
        groups.append({'id': node.pk, 'name': node.name, 'state': state,
            'state_label': {'maintenance': '维护中', 'disabled': '已停用', 'online': '在线', 'offline': '等待同步'}[state],
            'resources': observation.data if fresh else None, 'gpus': devices, 'model': ' / '.join(models),
            'gpu_available': sum(gpu['available_compute_percent'] for gpu in devices) / 100,
            'storage_tib': round(storage.get(node.pk, 0) / 1024, 1) if fresh else None})
    return groups


def request_capacity(user,overview=None,node=None):
    from .services import policy_for
    from .models import VolumeAllocation
    overview=overview if overview is not None else resource_overview(node)
    policy=policy_for(user)
    work=VolumeAllocation.objects.filter(user=user,kind='work').exclude(state='deleted').aggregate(n=Sum('size_gib'))['n'] or 0
    gpus=[]
    for gpu in overview['gpus']:
        ready=gpu.get('ready',True) and overview['fresh']
        gpus.append({'node_id':gpu['node_id'],'node_name':gpu['node_name'],'index':gpu['index'],'ready':ready,'compute_percent':gpu['available_compute_percent'] if ready else 0,
            'memory_gib':gpu['available_memory_gib'] if ready else 0,
            'memory_total_gib':round(gpu.get('usable_memory_mib',gpu.get('memory_mib',0))/1024,1)})
    resources=overview['resources'] or {}
    return {'fresh':overview['fresh'],'cpu':resources.get('cpu'),'memory_gib':resources.get('memory_gib'),'gpus':gpus,
        'storage_gib':overview['available_storage_gib'],
        'workspace_gib':{'available':None if policy.unlimited else max(0,policy.workspace_gib-work),
                         'total':None if policy.unlimited else policy.workspace_gib},
        'gpu_quota':None if policy.unlimited else {'compute_percent':policy.gpu_compute_percent,'memory_gib':policy.gpu_memory_gib}}


def member_list(query=''):
    rows = get_user_model().objects.select_related('profile__group').order_by('username')
    query = query.strip()
    if query:
        rows = rows.filter(Q(username__icontains=query) | Q(first_name__icontains=query) | Q(email__icontains=query))
    return rows


def container_list(query='',status='',mode=''):
    rows=ResourceRequest.objects.filter(workspace__isnull=False).select_related('user','workspace','image')
    query=query.strip()
    if query:
        rows=rows.filter(Q(purpose__icontains=query)|Q(user__username__icontains=query)|Q(workspace__object_name__icontains=query))
    if status:rows=rows.filter(status=status)
    if mode:rows=rows.filter(mode=mode)
    return rows.order_by('-created_at','-pk')


def group_list():
    return UserGroup.objects.annotate(member_count=Count('members')).order_by('-is_default', 'name')


def image_catalog(user, kind=None):
    query = images_for(user).select_related('owner').order_by('-created_at', '-pk')
    if kind == 'permanent':
        query = query.filter(verified=True, temporary=False)
    elif kind == 'temporary':
        query = query.filter(owner=user, temporary=True).exclude(state='deleted')
    return query


def managed_image_list():
    return ImageArtifact.objects.select_related('owner').exclude(state='deleted').order_by('state', 'recycle_after', '-created_at')


def image_summary(query):
    return {'ready_bytes': query.filter(state='ready').aggregate(total=Sum('size_bytes'))['total'] or 0,
            'waiting_count': query.filter(recycle_after__isnull=False).count(),
            'error_count': query.exclude(cleanup_error='').count()}


def pool_list():
    pools = list(StoragePool.objects.select_related('node').order_by('node__name', 'name'))
    for pool in pools:
        pool.available_gib = pool_available(pool)
        pool.percent = min(100, round(100*(pool.capacity_gib-pool.free_gib)/max(1, pool.capacity_gib)))
    return pools


def audit_list():
    return AuditLog.objects.select_related('actor').exclude(action='api_idempotency').order_by('-created_at')
