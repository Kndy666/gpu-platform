"""Administrator usage reports built from resource observations and task records."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Min
from django.utils import timezone
from django.utils.translation import gettext as tr

from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.nodes.capabilities import exclusive_accelerators
from gpu_platform_control.core.nodes.models import ComputeNode, SystemObservation
from gpu_platform_control.core.performance import performance_nodes
from gpu_platform_control.core.requests.models import ResourceRequest
from .boundary import parameters, result, staff_only
from .queries import resource_overview

UNKNOWN = "@unknown"


def add_capacity(row, ready, used):
    row["available"] += ready
    row["allocated"] += used if ready else 0


@staff_only
def current_usage(actor, *, now=None):
    now = now or timezone.now()
    overview = resource_overview()
    nodes = {node.pk: node for node in ComputeNode.objects.all()}
    performance = {row["id"]: row for row in performance_nodes(now=now)}
    running: dict[int, list] = defaultdict(list)
    active = ResourceRequest.objects.filter(status="running").select_related("user", "workspace").order_by("user__username")
    for task in active:
        running[task.node_id].append({
            "id": task.pk, "purpose": task.purpose,
            "name": task.user.get_full_name() or task.user.username,
            "username": task.user.username, "started_at": getattr(getattr(task, "workspace", None), "started_at", None),
        })
    model_totals: dict[tuple, dict] = {}
    node_totals: dict[int, dict] = {}
    for device in overview["gpus"]:
        node_id = device["node_id"]
        kind = "NPU" if exclusive_accelerators(nodes[node_id]) else "GPU"
        name = device["name"]
        model = model_totals.setdefault((name, kind), {
            "name": name, "kind": kind, "available": 0, "allocated": 0, "samples": [],
        })
        node = node_totals.setdefault(node_id, {"available": 0, "allocated": 0, "kind": kind})
        used = device["used_compute_percent"] / 100
        add_capacity(model, device["ready"], used)
        add_capacity(node, device["ready"], used)
        observed = performance[node_id]
        value = next((row.get("utilization_percent") for row in observed["metrics"].get("gpus", [])
                      if row["uuid"] == device["uuid"]), None) if observed["fresh"] else None
        if value is not None:
            model["samples"].append(value)
    host_cpu, cores, memory_used, memory_total = 0., 0., 0., 0.
    sampled = 0
    rows = []
    for node_id, node in nodes.items():
        observed = performance[node_id]
        data = observed["metrics"] if observed["fresh"] else {}
        cpu, memory = data.get("cpu", {}), data.get("memory", {})
        percent, cpu_cores = cpu.get("percent"), cpu.get("cores")
        if percent is not None and cpu_cores:
            host_cpu += percent * cpu_cores
            cores += cpu_cores
        if memory.get("used_gib") is not None and memory.get("total_gib"):
            memory_used += memory["used_gib"]
            memory_total += memory["total_gib"]
        sampled += bool(data)
        rows.append({
            "id": node_id, "name": node.name, "state": observed["status_label"], "sampled_at": observed["sampled_at"],
            "cpu": percent, "memory": memory.get("percent"), "running": running[node_id],
            **node_totals.get(node_id, {"available": 0, "allocated": 0, "kind": ""}),
        })
    models = [
        {**row, "actual": sum(row["samples"]) / len(row["samples"]) if row["samples"] else None}
        for row in sorted(model_totals.values(), key=lambda row: (row["kind"], row["name"]))
    ]
    capacity = {}
    for kind in ("GPU", "NPU"):
        family = [row for row in models if row["kind"] == kind]
        available = sum(row["available"] for row in family)
        allocated = sum(row["allocated"] for row in family)
        samples = [sample for row in family for sample in row["samples"]]
        capacity[kind] = {"available": available, "allocated": allocated,
                          "percent": allocated * 100 / available if available else None,
                          "actual": sum(samples) / len(samples) if samples else None}
    return result({
        "as_of": now, "online": sum(node.enabled and node.state == "online" for node in nodes.values()),
        "node_count": len(nodes), "sampled": sampled, "running_count": len(active), "nodes": rows, "models": models,
        "cpu": host_cpu / cores if cores else None,
        "memory": memory_used * 100 / memory_total if memory_total else None,
        "resources": overview["resources"], "capacity": capacity,
    })


def period(query, now):
    dates = []
    for key in ("from", "to"):
        value = query.get(key)
        try:
            parsed = date.fromisoformat(value) if value else None
            if parsed and not 2 <= parsed.year <= 9998:
                raise ValueError
        except (TypeError, ValueError) as error:
            raise ValidationError(tr("Enter valid dates.")) from error
        dates.append(parsed)
    first, last = dates
    if first and last and first > last:
        raise ValidationError(tr("The start date must be on or before the end date."))
    start = timezone.make_aware(datetime.combine(first, time.min)) if first else None
    end = timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min)) if last else now
    return start, min(now, end)


def requested_between(stamp, start, end):
    return (start is None or stamp >= start) and stamp < end


def account_label(account):
    return account.get_full_name() or account.username if account else tr("Unknown account")


def counters(label):
    return {"label": label, "applications": 0, "tasks": 0, "hours": 0., "gpu_hours": 0., "npu_hours": 0.}


def runtime(row, hours, gpu_hours, npu_hours):
    row["tasks"] += 1
    row["hours"] += hours
    row["gpu_hours"] += gpu_hours
    row["npu_hours"] += npu_hours


def task_id(target):
    try:
        return uuid.UUID(target)
    except ValueError:
        return None


def trend_segments(left, right, days):
    day = timezone.localdate(left)
    day = date.fromordinal(max(1, day.toordinal() - day.toordinal() % days))
    while left < right:
        boundary = timezone.make_aware(datetime.combine(day + timedelta(days=days), time.min))
        end = min(right, boundary)
        yield day.isoformat(), (end - left).total_seconds() / 3600
        day += timedelta(days=days)
        left = end


@staff_only
def usage_statistics(actor, *, query=None):
    query = parameters(query).dict()
    now = timezone.now()
    start, end = period(query, now)
    users = {str(user.pk): user for user in get_user_model().objects.order_by("username")}
    nodes = {str(node.pk): node for node in ComputeNode.objects.order_by("name")}
    inventory = {
        str(row.node_id): {entry["uuid"]: entry["name"] for entry in row.data.get("devices", [])}
        for row in SystemObservation.objects.filter(name="gpu_inventory")
    }
    model_options = {name for node_devices in inventory.values() for name in node_devices.values()}
    user_filter, node_filter, model_filter = (query.get(key, "") for key in ("account", "node", "model"))
    if (user_filter and user_filter not in users) or (node_filter and node_filter not in nodes):
        raise ValidationError(tr("Select an available account and node."))
    totals = counters("")
    by_user: dict[str, dict] = {}
    by_node: dict[str, dict] = {}
    by_model: dict[tuple[str, bool], dict] = {}
    trend: dict[str, dict[tuple[str, bool], float]] = defaultdict(lambda: defaultdict(float))

    audit = AuditLog.objects.filter(action="submit").order_by("created_at", "id")
    retained = ResourceRequest.objects.all()
    first = min((value for value in (
        audit.aggregate(first=Min("created_at"))["first"],
        retained.aggregate(first=Min("created_at"))["first"],
    ) if value), default=now)
    baseline = start or first
    days = max(1, ((end - baseline).days + 179) // 180)
    # Use matching audit events for native submissions, including tasks whose workspace was deleted.
    submissions: dict[uuid.UUID, dict] = {}
    for event in audit.values("target", "actor_id", "created_at", "detail").iterator():
        identifier = task_id(event["target"])
        if identifier is not None:
            submissions.setdefault(identifier, event)
    if model_filter and model_filter != UNKNOWN:
        model_options.add(model_filter)
    for task in retained.select_related("workspace", "node").iterator(chunk_size=256):
        event = submissions.pop(task.pk, None)
        owner, node = str(task.user_id), str(task.node_id)
        workspace = getattr(task, "workspace", None)
        allocation = workspace.allocations if workspace else {}
        entries = []
        for item in allocation.get("gpu", []):
            identity = item["device_id"]
            snapshot = allocation.get("devices", {}).get(identity, {})
            model = snapshot.get("model") or inventory.get(node, {}).get(identity) or UNKNOWN
            exclusive = snapshot.get("exclusive", exclusive_accelerators(task.node))
            share = 1. if exclusive else float(item["compute_percent"]) / 100
            entries.append((model, exclusive, share))
            model_options.add(model)
        if (user_filter and user_filter != owner) or (node_filter and node_filter != node):
            continue
        if model_filter:
            entries = [entry for entry in entries if entry[0] == model_filter]
            if not entries:
                continue
        owner_row = by_user.setdefault(owner, counters(account_label(users.get(owner))))
        node_row = by_node.setdefault(node, counters(task.node.name))
        buckets: dict[tuple[str, bool], float] = {}
        for model, exclusive, share in entries:
            buckets[model, exclusive] = buckets.get((model, exclusive), 0.) + share
        if requested_between(event["created_at"] if event else task.created_at, start, end):
            for row in (totals, owner_row, node_row):
                row["applications"] += 1
            for model, exclusive in buckets:
                by_model.setdefault((model, exclusive), counters(model))["applications"] += 1
        if not workspace or not workspace.started_at:
            continue
        stopped = workspace.ended_at or (now if task.status == "running" else None)
        if stopped is None:
            continue
        left = max(workspace.started_at, start) if start else workspace.started_at
        right = min(stopped, end)
        if right <= left:
            continue
        hours = (right - left).total_seconds() / 3600
        gpu_hours = hours * sum(share for (_, exclusive), share in buckets.items() if not exclusive)
        npu_hours = hours * sum(share for (_, exclusive), share in buckets.items() if exclusive)
        for row in (totals, owner_row, node_row):
            runtime(row, hours, gpu_hours, npu_hours)
        for (model, exclusive), share in buckets.items():
            model_row = by_model.setdefault((model, exclusive), counters(model))
            runtime(model_row, hours, 0. if exclusive else hours * share, hours * share if exclusive else 0.)
        if buckets:
            for timestamp, elapsed in trend_segments(left, right, days):
                for key, share in buckets.items():
                    trend[timestamp][key] += elapsed * share

    for event in submissions.values():
        owner, node = str(event["actor_id"]), str(event["detail"].get("node_id"))
        if (model_filter or user_filter and user_filter != owner or node_filter and node_filter != node
                or not requested_between(event["created_at"], start, end)):
            continue
        totals["applications"] += 1
        by_user.setdefault(owner, counters(account_label(users.get(owner))))["applications"] += 1
        by_node.setdefault(node, counters(nodes[node].name if node in nodes else tr("Unknown node")))["applications"] += 1

    def active_rows(rows):
        return [row for row in rows if row["tasks"] or row["applications"]]

    def model_label(model):
        return tr("Unknown accelerator") if model == UNKNOWN else model

    people = sorted(active_rows([
        {**row, "username": users[owner].username if owner in users else ""}
        for owner, row in by_user.items()
    ]), key=lambda row: (-row["applications"], -row["hours"], row["label"]))
    model_rows = [
        {**row, "label": model_label(model), "kind": "NPU" if exclusive else "GPU"}
        for (model, exclusive), row in sorted(by_model.items())
    ]
    timestamps = []
    if trend:
        first_bin = date.fromisoformat(min(trend))
        count = (date.fromisoformat(max(trend)) - first_bin).days // days + 1
        timestamps = [(first_bin + timedelta(days=offset * days)).isoformat() for offset in range(count)]
    return result({
        "section": "usage", "section_title": tr("Usage statistics"), "as_of": now, "first": first,
        "total_accounts": sum(account.is_active for account in users.values()),
        "applicants": sum(bool(row["applications"]) for row in people), "totals": totals, "people": people,
        "by_node": active_rows(by_node.values()), "by_model": model_rows,
        "filters": query, "account_options": [
            {"id": key, "label": f"{account_label(user)} ({user.username})"} for key, user in users.items()
        ], "node_options": [{"id": key, "label": node.name} for key, node in nodes.items()],
        "model_options": [{"id": key, "label": model_label(key)} for key in sorted(model_options)],
        "trend": {"dates": timestamps, "series": [
            {"name": model_label(model) + (" · NPU" if exclusive else " · GPU"),
             "values": [round(trend.get(stamp, {}).get((model, exclusive), 0.), 3) for stamp in timestamps]}
            for model, exclusive in sorted({key for values in trend.values() for key in values})
        ]},
        "ranking": sorted([{"name": row["label"], "gpu": round(row["gpu_hours"], 3), "npu": round(row["npu_hours"], 3)}
                           for row in people if row["gpu_hours"] or row["npu_hours"]],
                          key=lambda row: -(row["gpu"] + row["npu"]))[:12],
    })
