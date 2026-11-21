"""Quota conversions declared by modules and resolved against member policies."""

from decimal import Decimal, ROUND_FLOOR

from django.core.exceptions import ValidationError

from gpu_platform_contracts.resources import ResourceQuotaRule
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.policies import SYSTEM_STORAGE


def quota_rules(node=None, *, exclude_node_id=None):
    nodes = ComputeNode.objects.filter(enabled=True)
    if exclude_node_id is not None:
        nodes = nodes.exclude(pk=exclude_node_id)
    capabilities = [node.capabilities] if node is not None else nodes.values_list("capabilities", flat=True)
    result: dict[str, ResourceQuotaRule] = {}
    for report in capabilities:
        for module in report.get("modules", []):
            for key, value in module.get("quota_rules", {}).items():
                rule = ResourceQuotaRule.model_validate(value)
                if key in result and result[key] != rule:
                    raise ValidationError("resource_quota_rule_conflict", code="resource_quota_rule_conflict")
                result[key] = rule
    return result


def resource_limit(policy, key, *, rules):
    if key == SYSTEM_STORAGE:
        return policy.resources.get(key, Decimal(0))
    if policy.unlimited:
        return None
    rule = rules.get(key)
    if rule is None:
        return policy.resources.get(key, Decimal(0))
    values = policy.per_allocation if rule.basis == "per_allocation" else policy.resources
    amount = values.get(rule.source.key, Decimal(0))
    return (amount / rule.divisor).to_integral_value(rounding=ROUND_FLOOR) if amount is not None else None
