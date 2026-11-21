"""Stable API field names projected at the public service boundary."""

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from gpu_platform_control.core.policies import effective_policy, group_policy

RESOURCE_FIELDS = {'cpu': 'cpu', 'memory_gib': 'memory', 'gpu_compute_percent': 'gpu_compute',
    'gpu_memory_gib': 'gpu_memory', 'workspace_gib': 'work_storage', 'image_gib': 'image_storage'}
ALLOCATION_FIELDS = {'gpu_compute_per_card_percent': 'gpu_compute', 'gpu_memory_per_card_gib': 'gpu_memory'}


def gpu_device(data):
    fields = {
        'node_id', 'node_name', 'uuid', 'index', 'name', 'pci', 'memory_mib',
        'usable_memory_mib', 'ready', 'used_compute_percent', 'used_memory_mib',
        'available_compute_percent', 'available_memory_gib', 'used_memory_gib',
        'memory_total_gib',
    }
    extensions = dict(data.get('extensions', {}))
    extensions.update({key: value for key, value in data.items() if key not in fields | {'extensions'}})
    return {**{key: value for key, value in data.items() if key in fields}, 'extensions': extensions}


def number(value):
    amount = Decimal(str(value))
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def task_fields(task):
    allocations = task.resource_allocations.get('gpu', [])
    gpu_requests = [{'compute_percent': number(item.get('gpu_compute', 0)), 'memory_gib': number(item.get('gpu_memory', 0))} for item in allocations]
    return {'cpu': number(task.resource_quantities.get('cpu', 0)), 'memory_gib': number(task.resource_quantities.get('memory', 0)),
        'workspace_gib': task.volume_sizes.get('work', 0), 'gpu': len(allocations), 'gpu_requests': gpu_requests,
        'gpu_compute_total': number(task.resource_quantities.get('gpu_compute', 0)),
        'gpu_memory_total_gib': number(task.resource_quantities.get('gpu_memory', 0))}


def policy_fields(policy):
    values = policy.resources if hasattr(policy, 'resources') else policy['resources']
    per_allocation = policy.per_allocation if hasattr(policy, 'per_allocation') else policy['per_allocation']
    unlimited = policy.unlimited if hasattr(policy, 'unlimited') else policy['unlimited']
    return {} if unlimited else {
        **{field: number(values[key]) for field, key in RESOURCE_FIELDS.items() if values.get(key) is not None},
        **{field: number(per_allocation[key]) for field, key in ALLOCATION_FIELDS.items() if per_allocation.get(key) is not None},
    }


def group_fields(group):
    policy = group_policy(group)
    return {**policy_fields(policy), 'extensions': {'resources': policy['resources'], 'per_allocation': policy['per_allocation']}}


def quota_input(values):
    values = dict(values)
    extensions = values.pop('extensions', {}) or {}
    if not isinstance(extensions, dict) or set(extensions) - {'resources', 'per_allocation'}:
        raise ValidationError(_('Invalid resource extensions.'), code='invalid_extensions')
    resources = dict(extensions.get('resources', {}))
    per_allocation = dict(extensions.get('per_allocation', {}))
    for field, key in RESOURCE_FIELDS.items():
        if field in values:
            resources[key] = values.pop(field)
    for field, key in ALLOCATION_FIELDS.items():
        if field in values:
            per_allocation[key] = values.pop(field)
    values['resource_limits'], values['per_allocation_limits'] = resources, per_allocation
    if 'home_quota_gib' in values:
        values['home_size_gib'] = values.pop('home_quota_gib')
    return values


def gpu_request_limits(user, inventory=None, node=None):
    from gpu_platform_control.core.nodes.models import SystemObservation
    if inventory is None:
        inventory = SystemObservation.objects.filter(node=node, name='gpu_inventory').first()
    devices = [{'memory_gib': entry.get('memory_mib', 0) / 1024}
               for entry in inventory.data.get('devices', []) if entry.get('ready', True)] if inventory else []
    policy = effective_policy(user)
    return {'compute_per_card_percent': 100 if policy.unlimited else number(policy.per_allocation.get('gpu_compute') or 100),
        'memory_per_card_gib': max((item['memory_gib'] for item in devices), default=0) if policy.unlimited else number(policy.per_allocation.get('gpu_memory') or 0),
        'compute_total_percent': None if policy.unlimited or policy.resources.get('gpu_compute') is None else number(policy.resources['gpu_compute']),
        'memory_total_gib': None if policy.unlimited or policy.resources.get('gpu_memory') is None else number(policy.resources['gpu_memory']),
        'devices': devices}


def task_input(actor, data):
    from .catalog import request_options
    from gpu_platform_control.core.requests.models import ResourceRequest
    from .boundary import find

    data = dict(data)
    mode = data.get('mode', 'interactive')
    source = find(ResourceRequest, pk=data['source_environment']) if data.get('source_environment') else None
    options = request_options(actor, data.get('node') or (source.node_id if source else None), mode)
    values = dict(options['defaults'])
    allowed = {'node', 'purpose', 'image', 'source_environment', 'mode', 'starts_at', 'ends_at', 'working_dir', 'notes', 'environment'}
    values.update({key: value for key, value in data.items() if key in allowed})
    if source:
        values['image'] = source.image_id
        values['volume_sizes']['work'] = source.volume_sizes['work']
    extensions = data.get('extensions') or {}
    if not isinstance(extensions, dict) or set(extensions) - {'resources', 'allocations', 'volume_sizes'}:
        raise ValidationError(_('Invalid resource extensions.'), code='invalid_extensions')
    quantities = {**values['resource_quantities'], **extensions.get('resources', {})}
    values['resource_allocations'] = extensions.get('allocations', {})
    values['volume_sizes'].update(extensions.get('volume_sizes', {}))
    for definition in options['resources']:
        if definition['accounting'] == 'volume' and definition['volume_kind'] and definition['key'] in extensions.get('resources', {}):
            values['volume_sizes'][definition['volume_kind']] = extensions['resources'][definition['key']]
    for field, key in [('cpu', 'cpu'), ('memory_gib', 'memory')]:
        if field in data:
            quantities[key] = data[field]
    if 'workspace_gib' in data:
        values['volume_sizes']['work'] = data['workspace_gib']
    gpu_requests = data.get('gpu_requests')
    if gpu_requests is None:
        count = data.get('gpu', 0)
        gpu_requests = [{'compute_percent': 100, 'memory_gib': 8} for _ in range(count)]
    if 'gpu' in data and len(gpu_requests) != data['gpu']:
        raise ValidationError(_('Set compute and memory for each GPU.'))
    if gpu_requests:
        if 'gpu' in values['resource_allocations']:
            raise ValidationError(_('Invalid resource extensions.'), code='duplicate_allocation_intent')
        values['resource_allocations']['gpu'] = [{'gpu_compute': entry['compute_percent'], 'gpu_memory': entry['memory_gib']} for entry in gpu_requests]
    values['resource_quantities'] = quantities
    command = data.get('command') or ''
    values['argv'] = ['/bin/bash', '-c', command] if mode == 'batch' else []
    return values

def node_data(node, administrator=False):
    from gpu_platform_control.services.errors import execution_error

    return {
        "id": node.pk,
        "name": node.name,
        "architecture": node.architecture,
        "enabled": node.enabled,
        "maintenance": node.maintenance,
        "state": node.state,
        "state_label": node.get_state_display(),
        "last_seen": node.last_seen,
        "error": execution_error(node.error, node),
    }
