from django.contrib.auth import get_user_model
from .configuration import config
from .services import policy_for
from .gpu_math import COMPUTE,MEMORY

def queue_objects(user, policy):
    cfg = config()['platform']
    queue = f'platform-u{user.pk}'
    labels = {'platform.user': str(user.pk), 'platform.queue': 'user'}
    names = ['cpu', 'memory', COMPUTE, MEMORY, 'ephemeral-storage']
    quota = [{'name': name, 'nominalQuota': '0'} for name in names]
    if not policy.unlimited:
        limits = [str(policy.cpu), f'{policy.memory_gib}Gi', str(policy.gpu_compute_percent),str(policy.gpu_memory_gib*1024), f'{10 * policy.concurrent_tasks}Gi']
        for resource, limit in zip(quota, limits):
            resource['borrowingLimit'] = limit
    # Kueue's reserved `pods` resource gives each one-Pod task one non-borrowable slot.
    if not policy.unlimited:
        quota.append({'name': 'pods', 'nominalQuota': str(policy.concurrent_tasks), 'borrowingLimit': '0', 'lendingLimit': '0'})
    cluster = {'apiVersion': 'kueue.x-k8s.io/v1beta1', 'kind': 'ClusterQueue', 'metadata': {'name': queue, 'labels': labels},
        'spec': {'cohort': cfg['queue'], 'queueingStrategy': 'BestEffortFIFO',
            'namespaceSelector': {'matchLabels': {'platform.queue': 'enabled'}},
            'preemption': {'withinClusterQueue': 'Never', 'reclaimWithinCohort': 'Never'},
            'resourceGroups': [{'coveredResources': [resource['name'] for resource in quota],
                                'flavors': [{'name': 'platform-node', 'resources': quota}]}]}}
    local = {'apiVersion': 'kueue.x-k8s.io/v1beta1', 'kind': 'LocalQueue', 'metadata': {'name': f'user-{user.pk}', 'labels': labels},
             'spec': {'clusterQueue': queue}}
    return cluster, local

def matches(current, desired):
    if isinstance(desired, dict): return isinstance(current, dict) and all(k in current and matches(current[k], v) for k, v in desired.items())
    if isinstance(desired, list): return isinstance(current, list) and len(current) == len(desired) and all(matches(c, d) for c, d in zip(current, desired))
    return current == desired

def sync_user_queues(kube):
    ns = config()['platform']['workload_namespace']
    for user in get_user_model().objects.filter(is_active=True).select_related('profile__group'):
        cluster, local = queue_objects(user, policy_for(user))
        for obj, resource, namespace in [(cluster, 'clusterqueues', None), (local, 'localqueues', ns)]:
            current = kube.ensure(obj, resource, namespace, 'kueue.x-k8s.io/v1beta1')
            if not matches(current.get('spec', {}), obj['spec']):
                kube.call('PATCH', kube.path(resource, namespace, obj['metadata']['name'], 'kueue.x-k8s.io/v1beta1'), {'spec': obj['spec']})

def sync_pool_capacity(kube, nodes):
    cfg = config()['platform']
    current = kube.get('clusterqueues', None, cfg['queue'], 'kueue.x-k8s.io/v1beta1')
    if not current or current['metadata'].get('labels', {}).get('platform.queue') != 'pool':
        raise RuntimeError('等待全局队列初始化')
    ready = [n for n in nodes if not n.get('spec', {}).get('unschedulable') and any(c['type'] == 'Ready' and c['status'] == 'True' for c in n.get('status', {}).get('conditions', []))]
    alloc = ready[0]['status']['allocatable'] if len(ready) == 1 else {}
    names=['cpu','memory',COMPUTE,MEMORY,'ephemeral-storage']
    groups = [{'coveredResources': names, 'flavors': [{'name': 'platform-node',
        'resources': [{'name': name, 'nominalQuota': alloc.get(name, '0')} for name in names]}]}]
    if not matches(current['spec'].get('resourceGroups'), groups):
        kube.call('PATCH', kube.path('clusterqueues', None, cfg['queue'], 'kueue.x-k8s.io/v1beta1'), {'spec': {'resourceGroups': groups}})
