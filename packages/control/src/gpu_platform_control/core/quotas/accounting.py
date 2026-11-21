"""Quantity validation and accounting driven entirely by registered definitions."""

from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from gpu_platform_contracts.resources import ResourceDefinition
from .models import NodeResource, ResourceReservation
from gpu_platform_control.core.policies import effective_policy
from .rules import quota_rules, resource_limit


def problem(code, resource=None):
    raise ValidationError(_('The resource request exceeds its limits or is unavailable.'), code=code,
                          params={'resource': resource} if resource else None)


def quantity(value, definition=None):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        problem('resource_amount_invalid', getattr(definition, 'key', None))
    if not result.is_finite() or result < 0:
        problem('resource_amount_invalid', getattr(definition, 'key', None))
    if definition:
        if definition.integral and result != result.to_integral_value():
            problem('resource_amount_invalid', definition.key)
        if ((definition.minimum is not None and result < definition.minimum)
                       or (definition.maximum is not None and result > definition.maximum)):
            problem('resource_amount_invalid', definition.key)
        if definition.step is not None and (result - (definition.minimum or 0)) % definition.step:
            problem('resource_amount_invalid', definition.key)
    return result


def definitions(node):
    return {row.resource_id: row for row in NodeResource.objects.filter(node=node).select_related('resource')}


def allocation_constraints(node):
    return {
        definition["key"]: ResourceDefinition.model_validate(definition)
        for module in node.capabilities.get("modules", [])
        for definition in module.get("resources", [])
        if definition.get("per_allocation")
    }


def normalize_request(request):
    registered = definitions(request.node)
    constraints = allocation_constraints(request.node)
    policy = effective_policy(request.user)
    rules = quota_rules(request.node)
    values: dict[str, Decimal] = {}
    allocations = {}
    for group, entries in request.resource_allocations.items():
        if not isinstance(entries, list):
            problem('resource_amount_invalid', group)
        group_values = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry:
                problem('resource_amount_invalid', group)
            normalized = {}
            for key, value in entry.items():
                row = registered.get(key)
                if row is None or row.resource.allocation_group != group or not row.resource.requestable:
                    problem('resource_unavailable', key)
                amount = quantity(value, constraints.get(key, row.resource))
                limit = policy.per_allocation.get(key)
                if not policy.unlimited and limit is not None and amount > limit:
                    problem('resource_allocation_quota_exceeded', key)
                normalized[key] = str(amount)
                values[key] = values.get(key, Decimal(0)) + amount
            group_values.append(normalized)
        allocations[group] = group_values
    for key, value in request.resource_quantities.items():
        row = registered.get(key)
        if row is None or not row.resource.requestable:
            problem('resource_unavailable', key)
        amount = quantity(value, None if row.resource.per_allocation else row.resource)
        if key in values and amount != values[key]:
            problem('resource_allocation_total_mismatch', key)
        if row.resource.per_allocation and amount and key not in values:
            problem('resource_allocation_required', key)
        values[key] = amount
    sizes = dict(request.volume_sizes)
    sizes.setdefault('home', request.user.profile.home_size_gib)
    if set(sizes) != {'home', 'work'}:
        problem('volume_size_required')
    for kind, size in sizes.items():
        amount = quantity(size)
        if amount < 1 or amount != amount.to_integral_value():
            problem('volume_size_invalid')
        sizes[kind] = int(amount)
    if sizes['home'] != request.user.profile.home_size_gib:
        problem('home_size_mismatch')
    for key, row in registered.items():
        definition = row.resource
        if definition.accounting == 'volume' and definition.volume_kind:
            values[key] = Decimal(sizes[definition.volume_kind])
        amount = values.get(key, Decimal(0))
        limit = resource_limit(policy, key, rules=rules) if definition.accounting == 'task' else (
            policy.resources.get(key, None if policy.unlimited else Decimal(0))
        )
        if definition.has_member_quota and definition.accounting == 'volume' and limit is None:
            problem('resource_quota_required', key)
        if definition.has_member_quota and (not policy.unlimited or definition.accounting != 'task') and limit is not None and amount > limit:
            problem('resource_quota_exceeded', key)
        if definition.accounting == 'task' and amount > row.total:
            problem('node_capacity_exceeded', key)
    request.resource_quantities = {key: str(value) for key, value in values.items()}
    request.resource_allocations = allocations
    request.volume_sizes = sizes
    return values


def persistent_usage(owner, node, definition):
    if definition.accounting == 'volume':
        from gpu_platform_control.core.retention.models import VolumeAllocation
        query = VolumeAllocation.objects.filter(user=owner).exclude(state='deleted')
        if definition.scope == 'node':
            query = query.filter(node=node)
        if definition.volume_kind:
            query = query.filter(kind=definition.volume_kind)
        return Decimal(query.aggregate(total=Sum('size_gib'))['total'] or 0)
    if definition.accounting == 'image':
        from gpu_platform_control.core.retention.models import ImageArtifact
        from gpu_platform_control.core.sync.models import SyncJob
        images = ImageArtifact.objects.filter(owner=owner).exclude(state='deleted')
        copies = SyncJob.objects.filter(owner=owner, kind='image', publish_shared=False, state__in=['queued', 'running', 'cleaning'])
        if definition.scope == 'node':
            images, copies = images.filter(node=node), copies.filter(target=node)
        size = (images.aggregate(total=Sum('size_bytes'))['total'] or 0) + (copies.aggregate(total=Sum('reserved_bytes'))['total'] or 0)
        return Decimal(size) / Decimal(2**30)
    query = ResourceReservation.objects.filter(owner=owner, resource=definition)
    if definition.scope == 'node':
        query = query.filter(node=node)
    return query.aggregate(total=Sum('amount'))['total'] or Decimal(0)


def check_image_quota(owner, node, additional):
    policy = effective_policy(owner)
    for row in definitions(node).values():
        definition = row.resource
        if definition.accounting != "image":
            continue
        limit = policy.resources.get(definition.key, None if policy.unlimited else Decimal(0))
        if limit is not None and persistent_usage(owner, node, definition) + Decimal(additional) > limit:
            problem("resource_quota_exceeded", definition.key)
