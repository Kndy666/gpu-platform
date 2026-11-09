from decimal import Decimal
from .gpu_math import COMPUTE,MEMORY

def quantity(value):
    units = {'Ki': 2**10, 'Mi': 2**20, 'Gi': 2**30, 'Ti': 2**40, 'K': 1000, 'M': 1000**2, 'G': 1000**3, 'T': 1000**4, 'm': Decimal('.001'), 'n': Decimal('.000000001'), 'u': Decimal('.000001')}
    value = str(value)
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return Decimal(value[:-len(suffix)]) * factor
    return Decimal(value)

def pod_requests(pod):
    spec = pod.get('spec', {})
    regular, init = {}, {}
    for c in spec.get('containers', []):
        for k, v in c.get('resources', {}).get('requests', {}).items():
            regular[k] = regular.get(k, Decimal(0)) + quantity(v)
    for c in spec.get('initContainers', []):
        for k, v in c.get('resources', {}).get('requests', {}).items():
            init[k] = max(init.get(k, Decimal(0)), quantity(v))
    return {k: max(regular.get(k, 0), init.get(k, 0)) + quantity(spec.get('overhead', {}).get(k, 0)) for k in set(regular) | set(init)}

def resource_summary(nodes, pods):
    total, used = {}, {}
    ready_nodes = {n['metadata']['name'] for n in nodes if not n.get('spec', {}).get('unschedulable') and any(c['type'] == 'Ready' and c['status'] == 'True' for c in n.get('status', {}).get('conditions', []))}
    for node in nodes:
        if node['metadata']['name'] in ready_nodes:
            for key in ('cpu', 'memory', COMPUTE,MEMORY):
                total[key] = total.get(key, Decimal(0)) + quantity(node.get('status', {}).get('allocatable', {}).get(key, 0))
    for pod in pods:
        if pod.get('status', {}).get('phase') in {'Succeeded', 'Failed'} or pod.get('spec', {}).get('nodeName') not in ready_nodes:
            continue
        for key, val in pod_requests(pod).items():
            used[key] = used.get(key, Decimal(0)) + val
    result = {}
    for name, key, factor in [('gpu', COMPUTE, 100), ('gpu_memory_gib',MEMORY,1024),('cpu', 'cpu', 1), ('memory_gib', 'memory', 2**30)]:
        t, u = total.get(key, Decimal(0)) / factor, used.get(key, Decimal(0)) / factor
        result[name] = {'total': float(t), 'used': float(u), 'available': float(max(0, t - u)),
                        'used_percent': round(min(100, float(u / t * 100)), 1) if t else 0}
    return result
