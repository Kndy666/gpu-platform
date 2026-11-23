"""Portal read models consumed by both HTML views and the JSON API."""

from django.utils.translation import gettext_lazy as tr
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone
from gpu_platform_control.core.access import images_for
from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.nodes.models import ComputeNode, StoragePool, SystemObservation
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.accounts.models import UserGroup
from gpu_platform_control.core.services import ACTIVE, pool_available
from .projections import gpu_device
from gpu_platform_control.core.nodes.capabilities import accelerator_texts, accelerator_module


def resource_overview(node=None):
    observations = SystemObservation.objects.filter(
        name="resources", node__enabled=True, observed_at__gte=timezone.now() - timedelta(minutes=2)
    ).exclude(node__state="offline")
    if node:
        observations = observations.filter(node=node)
    observations = list(observations)
    fresh = bool(observations)
    combined: dict[str, dict[str, float]] = {}
    for observation in observations:
        for name, values in observation.data.items():
            if isinstance(values, dict) and "total" in values:
                result = combined.setdefault(name, {"total": 0, "used": 0, "available": 0})
                for key in ("total", "used", "available"):
                    result[key] += values.get(key, 0)
    for values in combined.values():
        values["used_percent"] = (
            round(min(100, values["used"] / values["total"] * 100), 1) if values["total"] else 0
        )
    pools = StoragePool.objects.filter(
        healthy=True, node__enabled=True, observed_at__gte=timezone.now() - timedelta(minutes=2)
    ).exclude(node__state="offline")
    if node:
        pools = pools.filter(node=node)
    available = sum((pool_available(pool) for pool in pools))
    counts = tasks_for_counts(node)
    inventories = SystemObservation.objects.filter(name="gpu_inventory").select_related("node")
    if node:
        inventories = inventories.filter(node=node)
    devices = [
        {
            **gpu_device(device), **accelerator_texts(inventory.node),
            "node_id": inventory.node_id,
            "node_name": inventory.node.name,
            "ready": device.get("ready", True)
            and inventory.node.enabled
            and (inventory.node.state != "offline")
            and (inventory.observed_at >= timezone.now() - timedelta(minutes=2)),
        }
        for inventory in inventories
        for device in inventory.data.get("devices", [])
    ]
    for d in devices:
        d["available_compute_percent"] = (
            max(0, 100 - d.get("used_compute_percent", 0)) if d.get("ready", True) else 0
        )
        d["available_memory_gib"] = (
            max(0, d.get("usable_memory_mib", d.get("memory_mib", 0)) - d.get("used_memory_mib", 0))
            / 1024
            if d["ready"]
            else 0
        )
        d["used_memory_gib"] = round(d.get("used_memory_mib", 0) / 1024, 1)
    counts_by_label: dict[str, int] = {}
    for device in devices:
        label = device["device_label"]
        counts_by_label[label] = counts_by_label.get(label, 0) + 1
    return {
        "accelerator_counts": [{"label": label, "count": count} for label, count in counts_by_label.items()],
        "resources": combined if fresh else None,
        "gpus": devices,
        "fresh": fresh,
        "available_storage_gib": available,
        "storage_tib": round(available / 1024, 1),
        **counts,
    }


def tasks_for_counts(node=None):
    from gpu_platform_control.core.requests.models import ResourceRequest

    query = ResourceRequest.objects.filter(node=node) if node else ResourceRequest.objects.all()
    counts = query.aggregate(
        running_count=Count("pk", filter=Q(status="running")),
        queued_count=Count("pk", filter=Q(status__in=ACTIVE - {"running"})),
    )
    return counts


def node_resource_groups(overview, node=None):
    cutoff = timezone.now() - timedelta(minutes=2)
    observations = {row.node_id: row for row in SystemObservation.objects.filter(name="resources")}
    storage: dict = {}
    for pool in StoragePool.objects.filter(healthy=True, observed_at__gte=cutoff).select_related("node"):
        storage[pool.node_id] = storage.get(pool.node_id, 0) + pool_available(pool)
    groups = []
    nodes = ComputeNode.objects.filter(pk=node.pk) if node else ComputeNode.objects.order_by("pk")
    for node in nodes:
        observation = observations.get(node.pk)
        fresh = bool(
            node.enabled and node.state != "offline" and observation and (observation.observed_at >= cutoff)
        )
        devices = sorted(
            (
                {
                    **gpu,
                    "memory_total_gib": gpu.get("usable_memory_mib", gpu.get("memory_mib", 0)) / 1024,
                }
                for gpu in overview["gpus"]
                if gpu["node_id"] == node.pk
            ),
            key=lambda gpu: gpu["index"],
        )
        models = list(
            dict.fromkeys(
                (
                    (gpu.get("name") or "GPU").removeprefix("NVIDIA ").removeprefix("GeForce ")
                    for gpu in devices
                )
            )
        )
        state = (
            "maintenance"
            if node.maintenance
            else "disabled"
            if not node.enabled
            else "online"
            if fresh
            else "offline"
        )
        groups.append(
            {
                "id": node.pk,
                "name": node.name, **accelerator_texts(node),
                "accelerator_resource_keys": [r["key"] for r in accelerator_module(node).get("resources", [])],
                "state": state,
                "state_label": {
                    "maintenance": tr("Maintenance"),
                    "disabled": tr("Disabled"),
                    "online": tr("Online"),
                    "offline": tr("Pending synchronization"),
                }[state],
                "resources": observation.data if fresh and observation else None,
                "gpus": devices,
                "model": " / ".join(models),
                "gpu_available": sum((gpu["available_compute_percent"] for gpu in devices)) / 100,
                "storage_tib": round(storage.get(node.pk, 0) / 1024, 1) if fresh else None,
            }
        )
    return groups


def request_capacity(user, overview=None, node=None):
    from gpu_platform_control.core.policies import effective_policy
    from gpu_platform_control.core.quotas.accounting import persistent_usage
    from gpu_platform_control.core.quotas.models import NodeResource
    from gpu_platform_control.core.quotas.rules import resource_limit
    from gpu_platform_control.core.nodes.selection import selected_node
    from .resource_display import node_resources
    node = node or selected_node()
    overview = overview if overview is not None else resource_overview(node)
    policy = effective_policy(user)
    definitions = {row.resource_id: row.resource for row in NodeResource.objects.filter(node=node).select_related("resource")}
    resources = [row for row in node_resources(node.pk)
                 if row["accounting"] != "volume" or row["volume_kind"] or definitions[row["key"]].requestable]
    for row in resources:
        definition = definitions[row["key"]]
        limit = None if policy.unlimited and definition.accounting == "task" else policy.resources.get(row["key"], None if policy.unlimited else 0)
        if definition.key == "system_storage":
            limit = resource_limit(policy, definition.key, rules={})
        used = 0
        if limit is not None and definition.accounting != "volume":
            used = persistent_usage(user, node, definition)
        row["quota_available"] = None if limit is None else max(0, limit - used)
        row["quota_total"] = limit
    return {"fresh": overview["fresh"], **accelerator_texts(node), "resources": resources, "gpus": [
        {"node_id": gpu["node_id"], "node_name": gpu["node_name"], "index": gpu["index"],
         "ready": gpu["ready"], "compute_percent": gpu["available_compute_percent"],
         "memory_gib": gpu["available_memory_gib"],
         "memory_total_gib": gpu.get("usable_memory_mib", gpu.get("memory_mib", 0)) / 1024}
        for gpu in overview["gpus"]
    ]}


def member_list(query=""):
    rows = get_user_model().objects.select_related("profile__group").order_by("username")
    query = query.strip()
    if query:
        rows = rows.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(email__icontains=query)
        )
    return rows


def container_list(query="", status="", mode=""):
    rows = ResourceRequest.objects.filter(workspace__isnull=False).select_related(
        "user", "workspace", "image"
    )
    query = query.strip()
    if query:
        rows = rows.filter(Q(purpose__icontains=query) | Q(user__username__icontains=query))
    if status:
        rows = rows.filter(status=status)
    if mode:
        rows = rows.filter(mode=mode)
    return rows.order_by("-created_at", "-pk")


def group_list():
    return UserGroup.objects.annotate(member_count=Count("members")).order_by("-is_default", "name")


def image_catalog(user, kind=None):
    query = images_for(user).select_related("owner").order_by("-created_at", "-pk")
    if kind == "permanent":
        query = query.filter(verified=True, temporary=False)
    elif kind == "temporary":
        query = query.filter(owner=user, temporary=True).exclude(state="deleted")
    return query


def managed_image_list():
    return (
        ImageArtifact.objects.select_related("owner")
        .exclude(state="deleted")
        .order_by("state", "recycle_after", "-created_at")
    )


def image_summary(query):
    return {
        "ready_bytes": query.filter(state="ready").aggregate(total=Sum("size_bytes"))["total"] or 0,
        "waiting_count": query.filter(Q(state="deleting") | Q(recycle_after__isnull=False)).count(),
        "error_count": query.exclude(cleanup_error="").count(),
    }


def pool_list():
    pools = list(StoragePool.objects.filter(
        Q(healthy=True) | Q(volumeallocation__state__in=["reserved", "bound", "deleting"])
    ).select_related("node").distinct().order_by("node__name", "name"))
    for pool in pools:
        pool.available_gib = pool_available(pool)
        pool.percent = min(100, round(100 * (pool.capacity_gib - pool.free_gib) / max(1, pool.capacity_gib)))
    return pools


def audit_list():
    return AuditLog.objects.select_related("actor").exclude(action="api_idempotency").order_by("-created_at")
