"""Canonical quantities selected from the member's registered resource policy."""

from gpu_platform_control.core.policies import effective_policy, group_policy


def policy_quantities(group):
    return group_policy(group)['resources']


def member_quantities(profile):
    policy = effective_policy(profile.user)
    return {key: None for key in policy.resources} if policy.unlimited else policy.resources
