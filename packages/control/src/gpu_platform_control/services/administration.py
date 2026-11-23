from datetime import datetime, timezone as utc_clock

from .boundary import authenticated, staff_only, find, parameters, result
from django.utils.translation import gettext_lazy as tr
from django.utils import timezone
from django.utils.formats import date_format
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from gpu_platform_control.core.configuration import config
from gpu_platform_control.services.audit_display import audit_presentation
from gpu_platform_control.core.image_lifecycle import keep_image, recycle_image
from gpu_platform_control.core.metrics_history import DAY, MetricsArchive
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import PlatformSettings, VolumeAllocation
from gpu_platform_control.core.accounts.models import UserGroup
from gpu_platform_control.core.nodes.selection import selected_node
from gpu_platform_control.core.services import audit
from gpu_platform_control.core.volumes import annotate_volume_usage, request_volume_deletion, volume_busy
from gpu_platform_control.core.access import image_actions_for, volumes_for
from gpu_platform_control.services.queries import (
    member_list,
    group_list,
    managed_image_list,
    image_summary,
    pool_list,
    audit_list,
)
from gpu_platform_control.services.queries import container_list
from gpu_platform_control.core.ssh_access import administrator_access


@staff_only
@transaction.atomic
def approvals(actor, *, values=None, query=None):
    from gpu_platform_control.core.requests.models import ExtensionRequest, ResourceRequest
    from gpu_platform_control.core.events.models import Notification, NotificationSettings
    from gpu_platform_control.core.notifications import event_labels
    from .notifications import notification_channels, set_notification_receiver

    settings = NotificationSettings.load()
    tab = parameters(query).get("tab", "pending")
    if tab not in {"pending", "push", "history"}:
        tab = "pending"
    if values is not None:
        action = values.get("action")
        if values.get("channel"):
            set_notification_receiver(actor, values["channel"], values.get("recipient", {}), defaults=True)
        elif action == "push":
            events = values.get("events", [])
            if not isinstance(events, list) or set(events) - set(event_labels()):
                raise ValidationError({"events": tr("Select valid notification events.")})
            minutes = int(values.get("reminder_minutes", 30))
            if not 1 <= minutes <= 1440:
                raise ValidationError({"reminder_minutes": tr("Enter 1–1440 minutes.")})
            settings.enabled = bool(values.get("enabled", False))
            settings.events, settings.reminder_minutes = events, minutes
            if settings.enabled:
                for channel in notification_channels(actor, defaults=True):
                    for name, specification in channel["fields"].items():
                        if (specification.get("required") and not channel["values"].get(name)
                                and not values.get("recipients", {}).get(channel["channel"], {}).get(name)):
                            raise ValidationError({"receiver:" + channel["channel"] + ":" + name: tr("Required receiver field.")})
            settings.full_clean()
            settings.save()
            for channel, recipient in values.get("recipients", {}).items():
                set_notification_receiver(actor, channel, recipient, defaults=True)
            audit(actor, "push_settings_updated", "platform", enabled=settings.enabled, events=events)
        elif action == "extension":
            from gpu_platform_control.core.approvals import review_extension

            review_extension(values.get("id"), actor, values.get("decision"), values.get("reason", ""))
        elif action == "request":
            from .pages import review

            review(actor, values.get("id"), values={**values, "action": values.get("decision")})
        else:
            raise ValidationError(tr("Select a valid review action."))
        return result(complete=True, notices=[("success", tr("Settings saved."))])
    title = {
        "pending": tr("Pending approval"),
        "push": tr("Notification settings"),
        "history": tr("Notification history"),
    }[tab]
    return result(
        {
            "section": "approvals",
            "section_title": title,
            "approval_tab": tab,
            "pending": ResourceRequest.objects.filter(status="submitted").select_related("user", "node"),
            "extensions": ExtensionRequest.objects.filter(status="pending").select_related(
                "request__user", "request__node"
            ),
            "form_options": notification_settings_options(actor),
            "channels": notification_channels(actor, defaults=True),
            "notifications": Notification.objects.order_by("-created_at")[:10],
        }
    )


@staff_only
def performance(actor, *, values=None, query=None, include_archive=True):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from gpu_platform_control.core.performance import performance_nodes

    selection = query.dict().get("node")
    node = ComputeNode.objects.filter(pk=selection).first() if selection and selection.isdigit() and len(selection) <= 18 else None
    archive = MetricsArchive.configured()
    archive_state = archive.status() if include_archive else archive.write_state()
    stamp = archive_state["last_write"]
    last_write = datetime.fromtimestamp(stamp, utc_clock.utc) if stamp is not None else None
    return result(
        {
            "section": "performance", "section_title": tr("Node performance"), "nodes": performance_nodes(node),
            "performance_node": node.pk if node else None,
            "archive": {**archive_state, "last_at": last_write} if include_archive else None,
            "archive_notice": _archive_notice(archive_state["state"], last_write),
        },
        notices=notices,
    )


@staff_only
@transaction.atomic
def nodes(actor, pk=None, *, values=None, query=None):
    from gpu_platform_control.core.nodes.models import default_node_id
    from gpu_platform_control.core.requests.models import ResourceRequest
    from gpu_platform_control.slots.configuration import cluster_config

    default_node_id()
    node = find(ComputeNode, pk=pk) if pk else None
    if values is not None:
        name = str(values.get("name", ""))
        from gpu_platform_contracts.config import ModuleSelection
        from gpu_platform_control.slots.configuration import save_host
        from gpu_platform_control.slots import load
        if not node and ComputeNode.objects.filter(name=name).exists():
            raise ValidationError({"name": tr("A node with this name already exists.")})
        current = cluster_config().hosts.get(node.name if node else name)
        if node and name != node.name:
            raise ValidationError({"name": tr("The node name cannot be changed.")})
        if current and "control" in current.roles:
            host = current
        else:
            module = values.get("transport")
            if not module:
                raise ValidationError({"transport": tr("Select a connection module.")})
            try:
                implementation = load("transport", module, options=values.get("transport_options", {}))
            except (ValueError, LookupError) as error:
                raise ValidationError(str(error)) from error
            host = current.model_copy(deep=True) if current else cluster_config().defaults.model_dump(mode="json")
            if isinstance(host, dict):
                from gpu_platform_contracts.config import HostConfig
                host = HostConfig.model_validate({**host, "roles": ["node"]})
            host.architecture = values.get("architecture", "amd64")
            host.address = values.get("address") or "127.0.0.1"
            host.slots["transport"] = ModuleSelection(module=module, options=implementation.context.options)
            if not current:
                from copy import deepcopy
                template = next((item for item in cluster_config().hosts.values()
                                 if "node" in item.roles and "control" not in item.roles), None)
                if template:
                    host.slots = {**deepcopy(template.slots), "transport": host.slots["transport"]}
            if node and node.architecture != host.architecture and ResourceRequest.objects.filter(
                node=node, workspace__cleanup_complete=False).exists():
                raise ValidationError(tr("Finish this node's tasks before changing its connection."))
            save_host(name, host.model_dump(mode="json"))
        node = node or ComputeNode()
        node.name, node.architecture = name, host.architecture
        node.enabled, node.maintenance = bool(values.get("enabled")), bool(values.get("maintenance"))
        node.full_clean()
        node.save()
        audit(
            actor, "node_updated", node.pk, name=node.name, enabled=node.enabled, maintenance=node.maintenance
        )
        return result(
            complete=True,
            notices=[
                ("success", tr("Node settings saved. Connection status will update on the next report."))
            ],
        )
    return result(
        {
            "section": "nodes",
            "section_title": tr("Compute nodes"),
            "nodes": ComputeNode.objects.order_by("pk"),
            "form_options": node_options(actor, pk, transport=query.get("transport")),
            "editing": node,
        }
    )


def page(query, queryset):
    return Paginator(queryset, 25).get_page(query.get("page"))


@staff_only
def containers(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from gpu_platform_control.core.requests.models import ResourceRequest
    from gpu_platform_control.services.pages import history_page

    node = selected_node(query.get("node"))
    search = query.get("q", "").strip()
    status = query.get("status", "")
    mode = query.get("mode", "")
    tasks = container_list(search, status, mode).filter(node=node)
    return result(
        {
            "section": "containers",
            "section_title": tr("Containers"),
            "node": node,
            "nodes": ComputeNode.objects.order_by("pk"),
            "tasks": history_page(query, tasks, size=25),
            "query": search,
            "status": status,
            "mode": mode,
            "states": ResourceRequest.State.choices,
            "ssh_access": administrator_access(actor),
        },
        notices=notices,
    )


@staff_only
def members(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    search = query.get("q", "").strip()
    users = member_list(search)
    return result(
        {
            "section": "members",
            "section_title": tr("Members and accounts"),
            "page": page(query, users),
            "query": search,
        },
        notices=notices,
    )


@staff_only
def member_edit(actor, pk=None, *, values=None, query=None):
    from .catalog import member_options
    from .commands import save_member

    options = member_options(actor, pk)
    member = options.get("member")
    if member and member.get("is_staff") and not actor.is_superuser and str(member["pk"]) != str(actor.pk):
        raise PermissionDenied
    if values is not None:
        saved = save_member(actor, values, member_id=pk)
        notices = [("success", tr("Member settings saved."))]
        if str(saved["pk"]) == str(actor.pk) and values.get("password"):
            notices.append(("refresh_session", ""))
        return result(complete=True, notices=notices)
    return result(
        {
            "section": "members",
            "section_title": member["username"] if member else tr("Add member"),
            "form_options": options,
            "member": member,
        }
    )


@staff_only
def groups(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    groups = group_list()
    return result(
        {"section": "groups", "section_title": tr("User groups"), "groups": groups}, notices=notices
    )


@staff_only
def group_edit(actor, pk=None, *, values=None, query=None):
    from .catalog import group_options
    from .commands import save_group

    if values is not None:
        save_group(actor, values, group_id=pk)
        return result(complete=True, notices=[("success", tr("Group saved."))])
    options = group_options(actor, pk)
    group = options.get("group")
    return result(
        {
            "section": "groups",
            "section_title": group["name"] if group else tr("Create group"),
            "form_options": options,
            "group": group,
        }
    )


@staff_only
@transaction.atomic
def group_delete(actor, pk, *, values=None, query=None):
    from gpu_platform_control.core.accounts.models import UserProfile

    group = find(UserGroup, pk=pk)
    others = UserGroup.objects.exclude(pk=group.pk)
    count = group.members.count()
    if values is not None:
        if not others.exists():
            raise ValidationError(tr("Keep at least one user group."))
        target = others.filter(pk=values.get("destination")).first() if values.get("destination") else None
        if count and target is None:
            raise ValidationError({"destination": tr("Select a group for these members.")})
        if target:
            UserProfile.objects.filter(group=group).update(group=target)
        if group.is_default:
            replacement = target or others.order_by("pk").first()
            replacement.is_default = True
            replacement.save(update_fields=["is_default"])
        audit(actor, "group_deleted", group.pk, name=group.name, destination=target.pk if target else None)
        group.delete()
        return result(complete=True, notices=[("success", tr("Group deleted."))])
    return result(
        {
            "section": "groups",
            "section_title": tr("Delete group"),
            "group": group,
            "form_options": {"groups": list(others.values("id", "name")), "member_count": count},
            "member_count": count,
        }
    )


@staff_only
def storage(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    pools = pool_list()
    nodes = {node.pk: {"node": node, "pools": []} for node in ComputeNode.objects.order_by("pk")}
    for pool in pools:
        nodes[pool.node_id]["pools"].append(pool)
    volumes = volumes_for(actor).exclude(state="deleted").order_by("user__username", "kind", "pk")
    visible_volumes = []
    for node_id, group in nodes.items():
        parameter = f"page_{node_id}"
        rows = Paginator(volumes.filter(node_id=node_id), 25).get_page(query.get(parameter))
        rows.object_list = list(rows.object_list)
        visible_volumes.extend(rows.object_list)
        group.update(
            page=rows,
            capacity_gib=sum(pool.capacity_gib for pool in group["pools"]),
            available_gib=sum(pool.available_gib for pool in group["pools"]),
            capacity_tib=sum((pool.capacity_gib for pool in group["pools"])) / 1024,
            available_tib=sum((pool.available_gib for pool in group["pools"])) / 1024,
            expanded=query.get("open_node") == str(node_id),
        )
        for direction, available, number in [
            ("previous", rows.has_previous(), rows.number - 1),
            ("next", rows.has_next(), rows.number + 1),
        ]:
            if available:
                query = query.copy()
                query[parameter], query["open_node"] = (str(number), str(node_id))
                group[direction + "_url"] = "?" + query.urlencode() + f"#storage-node-{node_id}"
    annotate_volume_usage(visible_volumes)
    return result(
        {"section": "storage", "section_title": tr("Storage"), "storage_nodes": list(nodes.values())},
        notices=notices,
    )


@staff_only
def volume_delete(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    volume = find(VolumeAllocation.objects.select_related("user", "pool"), pk=pk)
    if values is not None:
        try:
            request_volume_deletion(volume.pk, actor)
        except ValidationError as exc:
            notices.append(("error", "；".join(exc.messages)))
        else:
            notices.append(("success", tr("Volume deletion scheduled.")))
            return result(complete=True, notices=notices)
    return result(
        {
            "section": "storage",
            "section_title": tr("Delete volume"),
            "volume": volume,
            "busy": volume_busy(volume),
        },
        notices=notices,
    )


@staff_only
def images(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from gpu_platform_control.services.pages import history_page

    node = selected_node(query.get("node"))
    images = managed_image_list().filter(node=node)
    return result(
        {
            "section": "images",
            "section_title": tr("Images"),
            "node": node,
            "nodes": ComputeNode.objects.order_by("pk"),
            "page": history_page(query, images, size=25),
            **image_summary(images),
        },
        notices=notices,
    )


@staff_only
def settings(actor, *, values=None, query=None):
    settings = PlatformSettings.load()
    limits = {"workspace_retention_days": 3650, "snapshot_hours": 8760, "failed_snapshot_hours": 8760}
    if values is not None:
        for key, maximum in limits.items():
            value = int(values.get(key, 0))
            if not 1 <= value <= maximum:
                raise ValidationError({key: tr("Enter a value within the allowed range.")})
            setattr(settings, key, value)
        settings.full_clean()
        settings.save()
        audit(
            actor, "recycle_settings_updated", "platform", **{key: getattr(settings, key) for key in limits}
        )
        return result(
            complete=True,
            notices=[
                (
                    "success",
                    tr("Retention settings saved. They apply when tasks end and snapshot retention starts."),
                )
            ],
        )
    cfg = config()
    return result(
        {
            "section": "settings",
            "section_title": tr("Settings"),
            "form_options": {"initial": {key: getattr(settings, key) for key in limits}},
            "public_host": cfg["platform"]["public_host"],
            "ssh_start": cfg["platform"]["ssh_ports"][0],
            "ssh_end": cfg["platform"]["ssh_ports"][1],
        }
    )


@staff_only
def audits(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    rows = page(query, audit_list())
    for row in rows:
        for name, value in audit_presentation(row).items():
            setattr(row, name, value)
    return result({"section": "audits", "section_title": tr("Audit log"), "page": rows}, notices=notices)


@authenticated
def image_action(actor, pk, action, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    visible = image_actions_for(actor)
    image = find(visible, pk=pk)
    if action not in {"keep", "recycle"}:
        raise PermissionDenied
    try:
        (keep_image if action == "keep" else recycle_image)(pk, actor)
    except ValidationError as exc:
        notices.append(("error", "；".join(exc.messages)))
    else:
        notices.append(
            (
                "success",
                tr("Image retained.")
                if action == "keep"
                else tr("The image will be recycled in 24 hours. You can keep it before then."),
            )
        )
    if actor.is_staff and values.get("origin") == "management":
        return result(complete=True, notices=notices, identity=image.node_id)
    return result(complete=True, notices=notices, identity=image.node_id)


@staff_only
def node_options(actor, node_id=None, transport=None):
    from gpu_platform_control.slots.configuration import cluster_config

    node = find(ComputeNode, pk=node_id) if node_id else None
    from importlib.metadata import entry_points
    configuration = cluster_config()
    host = configuration.hosts.get(node.name) if node else None
    remote = next((item for item in configuration.hosts.values()
                   if "node" in item.roles and "control" not in item.roles), None)
    selected = host.slots.get("transport") if host else (remote.slots.get("transport") if remote else None)
    modules = []
    for entry in entry_points(group="gpu_platform.slots.transport"):
        implementation = entry.load()
        if implementation.descriptor.side in {"control", "both"}:
            modules.append({"name": entry.name, "schema": implementation.descriptor.parameters})
    initial: dict = {key: getattr(node, key) for key in ("name", "enabled", "maintenance")} if node else {"enabled": True}
    initial.update(
        architecture=host.architecture if host else "amd64", address=host.address if host else "127.0.0.1",
        transport=selected.module if selected else next((item["name"] for item in modules if item["name"] != "local"), "local"),
    )
    if transport and transport in {item["name"] for item in modules}:
        initial["transport"] = transport
    if selected and host and initial["transport"] == selected.module:
        initial.update({"transport:" + key: value for key, value in selected.options.items()})
    return {"initial": initial, "modules": modules, "editing": bool(node),
            "local": bool(host and "control" in host.roles)}



@staff_only
def notification_settings_options(actor):
    from gpu_platform_control.core.events.models import NotificationSettings
    from gpu_platform_control.core.notifications import event_labels
    from .read_models import materialize

    settings = NotificationSettings.load()
    return materialize(
        {
            "events": list(event_labels().items()),
            "initial": {key: getattr(settings, key) for key in ("enabled", "events", "reminder_minutes")},
        }
    )


def _archive_notice(state, last_write):
    warning = {
        "space": tr("Sample archiving paused due to low disk space"),
        "error": tr("Sample archive write failed"),
        "dropped": tr("Some samples were dropped"),
    }.get(state)
    if warning and last_write is not None:
        return tr("%(warning)s · Last successful write %(time)s") % {
            "warning": warning, "time": date_format(timezone.localtime(last_write), "SHORT_DATETIME_FORMAT"),
        }
    return warning


@staff_only
def performance_history(actor, *, query=None, chart_only=False):
    filters = parameters(query)
    query = filters.dict()
    nodes = list(ComputeNode.objects.order_by("pk").values("id", "name"))
    selected = set(filters.getlist("nodes"))
    if "nodes" not in filters:
        linked = query.get("node")
        selected = {linked} if any(str(node["id"]) == linked for node in nodes) else {
            str(node["id"]) for node in nodes
        }
    ranges = (1, 7, 30, 90, 365)
    days = next((days for days in ranges if str(days) == query.get("days")), 1)
    labels = {
        "cpu": (tr("CPU usage"), "%"),
        "cpu_temperature": (tr("CPU temperature"), "°C"),
        "memory": (tr("Memory usage"), "%"),
        "device_load": (tr("Accelerator utilization"), "%"),
        "device_memory": (tr("Accelerator memory usage"), "%"),
        "device_temperature": (tr("Accelerator temperature"), "°C"),
        "device_power": (tr("Accelerator power"), "W"),
        "disk": (tr("Disk usage"), "%"),
        "network_receive": (tr("Network receive rate"), "MiB/s"),
        "network_transmit": (tr("Network transmit rate"), "MiB/s"),
    }
    metric = query.get("metric", "cpu")
    if metric not in labels:
        metric = "cpu"
    archive = MetricsArchive.configured()
    end = int(timezone.now().timestamp())
    start = end - days * DAY
    step = archive.resolution([node["id"] for node in nodes if str(node["id"]) in selected], start, end, end)
    timestamps: list[int] = []
    series = []
    coverage_starts = []
    for color, node in enumerate(nodes):
        if str(node["id"]) not in selected:
            continue
        samples_by_time = archive.series(node["id"], start, end, metric, "", end, step=step, aggregate=True)
        rows = samples_by_time["series"]
        if samples_by_time["first_metric_at"] is not None:
            coverage_starts.append(samples_by_time["first_metric_at"])
        if not timestamps:
            timestamps = [row[0] for row in rows]
        samples = sum(row[4] for row in rows)
        series.append({
            **node, "color": color, "points": [row[1:] for row in rows],
            "mean": round(sum(row[1] * row[4] for row in rows if row[4]) / samples, 2) if samples else None,
            "peak": max((row[3] for row in rows if row[4]), default=None),
            "samples": samples,
        })
    first_sample = min(coverage_starts, default=None)
    coverage_note = None
    if first_sample is not None and first_sample - start >= step:
        local_start = timezone.localtime(datetime.fromtimestamp(first_sample, utc_clock.utc))
        coverage_note = tr("Samples for this selection begin %(time)s.") % {
            "time": date_format(local_start, "SHORT_DATETIME_FORMAT"),
        }
    history = {
        "timestamps": timestamps, "nodes": series, "step": step,
        "label": labels[metric][0], "unit": labels[metric][1], "days": days,
        "has_data": any(node["samples"] for node in series),
        "zone": timezone.get_current_timezone_name(),
        "coverage_note": coverage_note,
    }
    if chart_only:
        return result(history)
    return result({
        "history": history,
        "history_nodes": [{**node, "selected": str(node["id"]) in selected} for node in nodes],
        "history_ranges": ranges, "history_metric": metric,
        "history_metrics": {key: label for key, (label, _) in labels.items()},
    })
