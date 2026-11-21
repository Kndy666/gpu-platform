"""Resource registration and reservation independent of implementation technology."""

from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from gpu_platform_contracts.messages import CapabilityReport
from gpu_platform_control.core.nodes.models import ComputeNode, StoragePool, SystemObservation
from gpu_platform_control.core.accounts.models import UserProfile, UserGroup
from gpu_platform_control.core.quotas.accounting import allocation_constraints, quantity
from gpu_platform_control.core.quotas.models import RegisteredResource, NodeResource, ResourceQuota, ResourceReservation


def problem(code, **parameters):
    raise ValidationError(code, code=code, params=parameters)


def refines_precision(coarser, finer):
    if not coarser.per_allocation or not finer.per_allocation or finer.integral:
        return False
    old_step = coarser.step or (Decimal(1) if coarser.integral else None)
    new_step = finer.step
    old_min = coarser.minimum or Decimal(0)
    new_min = finer.minimum or Decimal(0)
    return (
        old_step is not None and new_step is not None and new_step < old_step
        and new_min <= old_min and coarser.maximum == finer.maximum
        and (old_min - new_min) % new_step == 0 and old_step % new_step == 0
    )


@transaction.atomic
def register_capabilities(node_id, report: CapabilityReport):
    node = ComputeNode.objects.select_for_update().get(pk=node_id)
    if report.node_id != node.name:
        problem("node_identity_mismatch")
    node.protocol_version = report.version
    report = report.model_copy(
        update={"images": [image.model_copy(update={"credentials": {}}) for image in report.images]}
    )
    node.capabilities = report.model_dump(mode="json")
    node.architecture = report.architecture
    node.last_seen = timezone.now()
    node.state = "online"
    node.error = ""
    from gpu_platform_control.core.quotas.quantities import policy_quantities
    from gpu_platform_control.core.policies import SYSTEM_STORAGE
    from gpu_platform_control.slots.configuration import cluster_config

    configured_groups = cluster_config().control.groups
    from gpu_platform_control.core.quotas.rules import quota_rules
    rules = quota_rules(exclude_node_id=node.pk)
    for descriptor in report.modules:
        for key, rule in descriptor.quota_rules.items():
            if key in rules and rules[key] != rule:
                problem("resource_quota_rule_conflict", resource=key)
            rules[key] = rule
    if any(rule.source.key in rules for rule in rules.values()):
        problem("resource_quota_rule_conflict")
    converted = {key for descriptor in report.modules for key in descriptor.quota_rules}
    seen = set()
    for descriptor in report.modules:
        declarations = [(definition, True) for definition in descriptor.resources]
        declarations.extend((rule.source, False) for rule in descriptor.quota_rules.values())
        for definition, physical in declarations:
            values = definition.model_dump(mode="json")
            key = values.pop("key")
            values.pop("default", None)
            resource, created = RegisteredResource.objects.select_for_update().get_or_create(key=key, defaults=values)
            if not created and (
                resource.unit != definition.unit
                or resource.requestable != definition.requestable
                or resource.scope != definition.scope
                or resource.accounting != definition.accounting
                or resource.volume_kind != definition.volume_kind
                or resource.allocation_group != definition.allocation_group
                or resource.allocation_field != definition.allocation_field
                or resource.per_allocation != definition.per_allocation
            ):
                problem("resource_definition_conflict", resource=key)
            if not created and any(
                getattr(resource, field) != getattr(definition, field)
                for field in ("integral", "minimum", "maximum", "step")
            ):
                if refines_precision(resource, definition):
                    for field in ("integral", "minimum", "step"):
                        setattr(resource, field, getattr(definition, field))
                    resource.save(update_fields=["integral", "minimum", "step"])
                elif not refines_precision(definition, resource):
                    problem("resource_definition_conflict", resource=key)
            if resource.label != values["label"]:
                resource.label = values["label"]
                resource.save(update_fields=["label"])
            for group in UserGroup.objects.all() if key != SYSTEM_STORAGE else ():
                defaults = policy_quantities(group)
                defaults.update(configured_groups.get(group.name, {}).get("resources", {}))
                if resource.has_member_quota and key not in converted and key in defaults:
                    ResourceQuota.objects.get_or_create(
                        group=group, resource=resource, defaults={"limit": defaults[key],
                            "per_allocation_limit": configured_groups.get(group.name, {}).get("per_allocation", {}).get(key)}
                    )
            if physical:
                seen.add(key)
                NodeResource.objects.update_or_create(
                    node=node,
                    resource=resource,
                    defaults={
                        "total": report.resources.total.get(key, 0),
                        "used": report.resources.used.get(key, 0),
                    },
                )
    NodeResource.objects.filter(node=node).exclude(resource_id__in=seen).delete()
    totals, usage = report.resources.total, report.resources.used
    read_resources = {}
    for key, total in totals.items():
        used = usage.get(key, Decimal(0))
        read_resources[key] = {
            "total": float(total),
            "used": float(used),
            "available": float(max(Decimal(0), total - used)),
            "used_percent": round(float(used / total * 100), 1) if total else 0,
        }
    SystemObservation.objects.update_or_create(node=node, name="resources", defaults={"data": read_resources})
    devices = [
        {
            "uuid": gpu.id,
            "index": index,
            "name": gpu.name,
            "ready": gpu.compute_percent > 0,
            "memory_mib": float(gpu.memory_gib * 1024),
            "usable_memory_mib": float(gpu.memory_gib * 1024),
            "used_compute_percent": float(gpu.used_compute_percent),
            "used_memory_mib": float(gpu.used_memory_gib * 1024),
        }
        for index, gpu in enumerate(report.gpus)
    ]
    SystemObservation.objects.update_or_create(
        node=node, name="gpu_inventory", defaults={"data": {"devices": devices}}
    )
    from gpu_platform_control.core.retention.models import VolumeAllocation
    for pool in report.resources.pools:
        record, _ = StoragePool.objects.update_or_create(
            node=node, name=pool.name,
            defaults={
                "capacity_gib": int(pool.capacity_gib), "free_gib": int(pool.free_gib),
                "reserved_gib": int(pool.reserved_gib), "buffer_percent": pool.buffer_percent,
                "healthy": pool.healthy, "observed_at": timezone.now(),
            },
        )
        storage_class = pool.metadata.get("storage_class")
        if storage_class:
            VolumeAllocation.objects.filter(node=node, handoff__metadata__storage_class=storage_class).update(pool=record)
    if report.resources.pools:
        StoragePool.objects.filter(node=node).exclude(name__in=[pool.name for pool in report.resources.pools]).update(healthy=False)
        StoragePool.objects.filter(node=node, volumeallocation__isnull=True).exclude(
            name__in=[pool.name for pool in report.resources.pools]).delete()
    from gpu_platform_control.core.retention.models import ImageArtifact

    for image in report.images:
        if image.node_id != node.name or image.architecture != node.architecture:
            problem("image_catalog_mismatch")
        ImageArtifact.objects.update_or_create(
            node=node,
            reference=image.reference,
            defaults={
                "name": image.metadata.get("name", image.id),
                "node_image_id": image.id,
                "node_image_owner": image.owner,
                "architecture": image.architecture,
                "size_bytes": image.size_bytes,
                "digest": image.digest,
                "owner": None,
                "shared": True,
                "verified": True,
                "state": "ready",
            },
        )
    node.save()
    from .bootstrap import ensure_default_image

    ensure_default_image(node)


def available_resources(node=None):
    from django.db.models import Q
    from gpu_platform_control.core.quotas.rules import quota_rules

    sources = {rule.source.key for rule in quota_rules().values()} if node is None else set()
    query = RegisteredResource.objects.filter(Q(noderesource__node__enabled=True) | Q(key__in=sources))
    if node is not None:
        query = query.filter(noderesource__node=node)
    rows = list(query.distinct().values("key", "unit", "label", "scope", "integral", "requestable", "accounting", "volume_kind", "allocation_group", "allocation_field", "per_allocation", "minimum", "maximum", "step"))
    if node is not None:
        constraints = allocation_constraints(node)
        for row in rows:
            if definition := constraints.get(row["key"]):
                row.update({field: getattr(definition, field) for field in ("integral", "minimum", "maximum", "step")})
    return rows


@transaction.atomic
def reserve_resources(owner, node, subject, quantities):
    profile = UserProfile.objects.select_for_update().get(user=owner)
    node = ComputeNode.objects.select_for_update().get(pk=node.pk)
    if not node.enabled or node.maintenance or node.state != "online":
        problem("node_unavailable")
    capacities = {
        row.resource_id: row for row in NodeResource.objects.select_related("resource").filter(node=node)
    }
    constraints = allocation_constraints(node)
    quotas = {row.resource_id: row.limit for row in ResourceQuota.objects.filter(group=owner.profile.group)}
    from gpu_platform_control.core.quotas.quantities import member_quantities

    quotas.update(member_quantities(profile))
    from gpu_platform_control.core.policies import SYSTEM_STORAGE, effective_policy
    from gpu_platform_control.core.quotas.rules import quota_rules, resource_limit
    policy = effective_policy(owner)
    rules = quota_rules(node)
    normalized = {}
    for key, value in quantities.items():
        if key not in capacities:
            problem("resource_unavailable", resource=key)
        definition = capacities[key].resource
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            problem("resource_amount_invalid", resource=key)
        if (
            not amount.is_finite()
            or amount < 0
            or (constraints.get(key, definition).integral and amount != amount.to_integral_value())
        ):
            problem("resource_amount_invalid", resource=key)
        if definition.accounting != "task" or not definition.requestable:
            problem("resource_not_requestable", resource=key)
        amount = quantity(amount, None if definition.per_allocation else definition)
        others = ResourceReservation.objects.filter(owner=owner, resource_id=key).exclude(subject=subject)
        if definition.scope == "node":
            others = others.filter(node=node)
        used = others.aggregate(total=Sum("amount"))["total"] or Decimal(0)
        limit = resource_limit(policy, key, rules=rules) if key in rules or key == SYSTEM_STORAGE else (
            None if policy.unlimited else quotas.get(key, Decimal(0))
        )
        if limit is not None and used + amount > limit:
            problem("resource_quota_exceeded", resource=key)
        existing = ResourceReservation.objects.filter(owner=owner, subject=subject, resource_id=key).first()
        if existing and existing.node_id != node.pk:
            problem("reservation_node_conflict")
        previous = existing.amount if existing else Decimal(0)
        node_reserved = ResourceReservation.objects.filter(node=node, resource_id=key).aggregate(
            total=Sum("amount")
        )["total"] or Decimal(0)
        capacity = capacities[key]
        projected = max(capacity.used, node_reserved) + max(Decimal(0), amount - previous)
        if projected > capacity.total:
            problem("node_capacity_exceeded", resource=key)
        normalized[key] = amount
    for key, amount in normalized.items():
        ResourceReservation.objects.update_or_create(
            owner=owner, subject=subject, resource_id=key, defaults={"node": node, "amount": amount}
        )
    ResourceReservation.objects.filter(owner=owner, subject=subject).exclude(
        resource_id__in=normalized
    ).delete()
    return normalized


@transaction.atomic
def release_resources(owner, subject):
    UserProfile.objects.select_for_update().get(user=owner)
    return ResourceReservation.objects.filter(owner=owner, subject=subject).delete()[0]
