"""Resolve a member's explicit settings over their group's defaults."""
from copy import copy
from .models import GROUP_QUOTA_FIELDS, UserProfile


def profile_for(user):
    return UserProfile.objects.select_related('group').get(user_id=user.pk)


def effective_policy(user):
    profile = profile_for(user)
    group = profile.group
    policy = copy(group)
    overrides = profile.quota_overrides
    if 'unlimited' in overrides:
        policy.unlimited = overrides['unlimited']
    for field in GROUP_QUOTA_FIELDS:
        if field in overrides:
            setattr(policy, field, overrides[field])
    return policy


def extension_limit(user):
    profile = profile_for(user)
    return profile.group.extension_limit if profile.extension_limit is None else profile.extension_limit


def approval_mode(user):
    profile = profile_for(user)
    return profile.approval_mode or profile.group.approval_mode
