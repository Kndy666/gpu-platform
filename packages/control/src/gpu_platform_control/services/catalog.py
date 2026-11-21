"""Materialized choices and policies shared by form and API clients."""

from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import get_language
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_control.core.accounts.models import UserGroup
from gpu_platform_control.core.nodes.models import ComputeNode, default_node_id
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.policies import effective_policy, group_policy
from gpu_platform_control.core.features import provides
from .boundary import authenticated, staff_only, find
from .read_models import materialize
from .resources import available_resources
from gpu_platform_control.core.quotas.rules import quota_rules, resource_limit


def resource_options(node=None, policy=None, mode='interactive'):
    rows = available_resources(node)
    rules = quota_rules(node)
    defaults = {resource["key"]: resource.get("default") for module in (node.capabilities.get("modules", []) if node else [])
                for resource in module.get("resources", [])}
    for row in rows:
        row['has_member_quota'] = row['key'] not in rules and (row['accounting'] != 'volume' or row['volume_kind'] == 'work')
        value = defaults.get(row["key"])
        row["default"] = value.get(mode) if isinstance(value, dict) else value
        row['label_text'] = LocalizedText.model_validate(row['label']).resolve(get_language() or 'en')
        row['limit'] = None if policy and policy.unlimited and row['accounting'] == 'task' else (policy.resources.get(row['key'], None if policy.unlimited else Decimal(0)) if policy else None)
        if policy and (row['key'] in rules or row['key'] == 'system_storage'):
            row['limit'] = resource_limit(policy, row['key'], rules=rules)
        row['per_allocation_limit'] = None if policy and policy.unlimited else (policy.per_allocation.get(row['key']) if policy else None)
    return materialize(rows)


@authenticated
def request_options(actor, node_id=None, mode='interactive'):
    from gpu_platform_control.core.quotas.models import ResourceReservation
    from django.db.models import Sum

    node = find(ComputeNode, pk=node_id or default_node_id())
    policy = effective_policy(actor)
    images = ImageArtifact.objects.filter(node=node, verified=True, state='ready').filter(Q(owner=actor) | Q(shared=True)).order_by('-shared', '-created_at')
    environments = ResourceRequest.objects.filter(node=node, user=actor, mode='interactive', status='running').exclude(workspace__stop_requested=True)
    now = timezone.localtime().replace(second=0, microsecond=0)
    duration = 4 if policy.unlimited else min(4, policy.interactive_hours)
    defaults = {'node': node.pk, 'mode': mode, 'image': images.values_list('pk', flat=True).first(),
                'volume_sizes': {'home': actor.profile.home_size_gib, 'work': 20},
                'working_dir': '/workspace', 'resource_quantities': {}, 'resource_allocations': {}}
    if mode == 'interactive':
        defaults.update(starts_at=now, ends_at=now+timedelta(hours=duration))
    resources = resource_options(node, policy, mode)
    for definition in resources:
        if definition['accounting'] == 'task' and definition['requestable'] and not definition['allocation_group']:
            amount = definition.get('default')
            if amount is None:
                amount = definition['minimum'] if definition['minimum'] is not None else Decimal(1)
            if definition['key'] == 'system_storage' and definition['limit'] is not None:
                reserved = ResourceReservation.objects.filter(owner=actor, resource_id=definition['key']).aggregate(
                    total=Sum('amount')
                )['total'] or Decimal(0)
                available = max(Decimal(0), Decimal(definition['limit']) - reserved)
                definition['quota_available'] = int(available)
                amount = min(Decimal(amount), int(available)) if available >= 1 else None
            defaults['resource_quantities'][definition['key']] = str(amount) if amount is not None else None
        if definition['accounting'] == 'volume' and definition['volume_kind'] == 'work' and definition['limit'] is not None:
            defaults['volume_sizes']['work'] = max(1, min(20, int(definition['limit'])))
    if mode == "batch":
        allocations: dict[str, dict[str, str]] = {}
        for definition in resources:
            group = definition["allocation_group"]
            amount = definition.get("default")
            if group and amount is not None:
                limit = definition.get("per_allocation_limit")
                amount = min(Decimal(str(amount)), Decimal(str(limit))) if limit is not None else Decimal(str(amount))
                allocations.setdefault(group, {})[definition["key"]] = str(amount)
        for group, values in allocations.items():
            if all(Decimal(amount) > 0 for amount in values.values()):
                defaults["resource_allocations"][group] = [values]
    return materialize({'node': node, 'nodes': list(ComputeNode.objects.filter(enabled=True)),
        'images': [{'id': image.pk, 'name': image.name, 'shared': image.shared} for image in images],
        'environments': [{'id': task.pk, 'purpose': task.purpose, 'volume_sizes': task.volume_sizes} for task in environments],
        'source_workspaces': {str(task.pk): {'size_gib': task.volume_sizes.get('work', 0)} for task in environments},
        'resources': resources, 'defaults': defaults, 'features': node.capabilities.get('features', {})})


@staff_only
def group_options(actor, group_id=None):
    from gpu_platform_control.core.policies import DEFAULT_SYSTEM_STORAGE_GIB

    group = find(UserGroup, pk=group_id) if group_id else UserGroup()
    values: dict = group_policy(group) if group.pk else {'unlimited': False, 'concurrent_tasks': 1,
        'interactive_hours': 120, 'resources': {}, 'per_allocation': {}}
    resources = [row for row in resource_options() if row['has_member_quota']]
    if not group.pk and any(row['key'] == 'system_storage' for row in resources):
        values['resources']['system_storage'] = DEFAULT_SYSTEM_STORAGE_GIB
    initial = {**values, 'name': group.name, 'make_default': group.is_default,
               'resource_limits': values.pop('resources'), 'per_allocation_limits': values.pop('per_allocation'),
               'extension_limit': group.extension_limit, 'approval_mode': group.approval_mode,
               'home_size_gib': group.home_size_gib}
    return materialize({'initial': initial, 'resources': resources, 'group': group if group.pk else None})


@staff_only
def member_options(actor, member_id=None):
    user = find(get_user_model(), pk=member_id) if member_id else None
    groups = list(UserGroup.objects.order_by('-is_default', 'name'))
    group = user.profile.group if user else (groups[0] if groups else None)
    values = group_policy(group) if group else {'unlimited': False, 'resources': {}, 'per_allocation': {}, 'concurrent_tasks': 1, 'interactive_hours': 120}
    if user:
        policy = effective_policy(user)
        values.update(unlimited=policy.unlimited, concurrent_tasks=policy.concurrent_tasks,
                      interactive_hours=policy.interactive_hours, resources=policy.resources, per_allocation=policy.per_allocation)
    initial = {'group': group.pk if group else None, 'home_size_gib': user.profile.home_size_gib if user else group.home_size_gib if group else 50,
        'unlimited': values['unlimited'], 'resource_limits': values['resources'],
        'per_allocation_limits': values['per_allocation'], 'concurrent_tasks': values['concurrent_tasks'],
        'interactive_hours': values['interactive_hours'],
        'extension_limit': user.profile.extension_limit if user else group.extension_limit if group else 1,
        'approval_mode': user.profile.approval_mode if user else group.approval_mode if group else 'manual',
        'is_active': user.is_active if user else True, 'is_staff': user.is_staff if user else False}
    if user:
        initial.update({key: getattr(user, key) for key in ('username', 'first_name', 'email')})
    return materialize({'initial': initial, 'resources': [row for row in resource_options() if row['has_member_quota'] and row['key'] != 'system_storage'], 'member': user,
        'groups': [{'id': item.pk, 'name': item.name} for item in groups],
        'group_defaults': {str(item.pk): {**group_policy(item), 'extension_limit': item.extension_limit, 'home_size_gib': item.home_size_gib,
            'approval_mode': item.approval_mode} for item in groups}, 'can_grant_admin': actor.is_superuser})


@authenticated
def build_options(actor):
    nodes = [node for node in ComputeNode.objects.filter(enabled=True) if provides(node, 'build') and provides(node, 'registry')]
    return materialize({'nodes': nodes, 'initial': {'node': nodes[0].pk if nodes else None}})
