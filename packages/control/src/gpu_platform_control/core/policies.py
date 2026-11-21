"""Core limits and registered resource policies for a member."""

from dataclasses import dataclass
from decimal import Decimal
from gpu_platform_control.core.accounts.models import UserProfile
from gpu_platform_control.core.quotas.models import ResourceQuota


@dataclass(frozen=True)
class Policy:
    unlimited: bool
    concurrent_tasks: int | None
    interactive_hours: int | None
    extension_limit: int
    approval_mode: str
    resources: dict[str, Decimal | None]
    per_allocation: dict[str, Decimal | None]


def group_policy(group):
    from gpu_platform_control.slots.configuration import cluster_config

    configured = cluster_config().control.groups.get(group.name, {})
    resources = dict(configured.get('resources', {}))
    per_allocation = dict(configured.get('per_allocation', configured.get('per_allocation_limits', {})))
    for quota in ResourceQuota.objects.filter(group=group).select_related('resource'):
        resources[quota.resource_id] = quota.limit
        if quota.resource.per_allocation:
            per_allocation[quota.resource_id] = quota.per_allocation_limit
    return {
        'unlimited': group.unlimited,
        'concurrent_tasks': group.concurrent_tasks,
        'interactive_hours': group.interactive_hours,
        'resources': {key: str(value) if value is not None else None for key, value in resources.items()},
        'per_allocation': {key: str(value) if value is not None else None for key, value in per_allocation.items()},
    }


def settings_from_group(group):
    return {'resource_limits': group_policy(group), 'extension_limit': group.extension_limit,
            'approval_mode': group.approval_mode}


def profile_for(user):
    return UserProfile.objects.select_related('group').get(user_id=user.pk)


def effective_policy(user):
    profile = profile_for(user)
    values = group_policy(profile.group)
    stored = profile.resource_limits
    values.update({key: stored[key] for key in ('unlimited', 'concurrent_tasks', 'interactive_hours') if key in stored})
    for key in ('resources', 'per_allocation'):
        values[key].update(stored.get(key, {}))
    return Policy(
        unlimited=values['unlimited'], concurrent_tasks=values['concurrent_tasks'],
        interactive_hours=values['interactive_hours'], extension_limit=profile.extension_limit,
        approval_mode=profile.approval_mode,
        resources={key: Decimal(value) if value is not None else None for key, value in values['resources'].items()},
        per_allocation={key: Decimal(value) if value is not None else None for key, value in values['per_allocation'].items()},
    )


def extension_limit(user):
    return profile_for(user).extension_limit


def approval_mode(user):
    return profile_for(user).approval_mode
