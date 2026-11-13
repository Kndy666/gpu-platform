"""Independent member settings initialized from a group's defaults."""
from copy import copy
from .models import GROUP_QUOTA_FIELDS, UserProfile


def profile_for(user):
    return UserProfile.objects.select_related('group').get(user_id=user.pk)


def settings_from_group(group):
    return {'resource_limits': {'unlimited': group.unlimited,
                               **{name: getattr(group, name) for name in GROUP_QUOTA_FIELDS}},
            'extension_limit': group.extension_limit, 'approval_mode': group.approval_mode}


def effective_policy(user):
    profile = profile_for(user)
    group = profile.group
    policy = copy(group)
    policy.unlimited = profile.resource_limits['unlimited']
    for field in GROUP_QUOTA_FIELDS:
        setattr(policy, field, profile.resource_limits[field])
    policy.extension_limit = profile.extension_limit
    policy.approval_mode = profile.approval_mode
    return policy


def extension_limit(user):
    profile = profile_for(user)
    return profile.extension_limit


def approval_mode(user):
    profile = profile_for(user)
    return profile.approval_mode
