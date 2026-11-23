from .errors import execution_error
from .boundary import authenticated, staff_only, find, NotFound, parameters, result
from django.utils.translation import gettext_lazy as tr
from datetime import timedelta
from typing import Any
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from gpu_platform_control.core.configuration import config
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.accounts.models import SSHKey
from gpu_platform_control.core.retention.models import (
    VolumeAllocation,
    ImageArtifact,
    ImageBuild,
    EnvironmentSnapshot,
)
from gpu_platform_control.core.nodes.models import SystemObservation
from gpu_platform_control.core.services import audit, approve, reject, request_stop, extend
from gpu_platform_control.core.access import tasks_for
from gpu_platform_control.services.queries import resource_overview, image_catalog


def history_page(query, queryset, parameter="page", size=20):
    page = Paginator(queryset, size).get_page(query.get(parameter))
    query = query.copy()
    for name, available, number in (
        ("previous_url", page.has_previous(), page.number - 1),
        ("next_url", page.has_next(), page.number + 1),
    ):
        if available:
            query[parameter] = number
            setattr(page, name, "?" + query.urlencode())
    return page


@authenticated
def dashboard(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    tasks = ResourceRequest.objects.filter(user=actor).select_related("user", "workspace")[:8]
    from gpu_platform_control.services.queries import node_resource_groups

    overview = resource_overview()
    from .resource_display import node_resources

    nodes = node_resource_groups(overview)
    for node in nodes:
        accelerator_keys = node["accelerator_resource_keys"]
        node["display_resources"] = [row for row in node_resources(node["id"]) if row["key"] not in accelerator_keys and not row["volume_kind"] and not row["allocation_group"] and row["accounting"] != "image"]
    return result({**overview, "node_groups": nodes, "tasks": tasks}, notices=notices)


@authenticated
def keys(actor, *, values=None, query=None):
    if values is not None:
        from gpu_platform_control.core.services import parse_ssh_key

        public_key, fingerprint = parse_ssh_key(values.get("public_key", ""))
        name = str(values.get("name", "")).strip()
        if not name:
            raise ValidationError({"name": tr("Enter a key name.")})
        with transaction.atomic():
            key = SSHKey.objects.filter(user=actor, fingerprint=fingerprint).first()
            if key and key.enabled:
                raise ValidationError({"public_key": tr("This public key is already registered.")})
            restored = key is not None
            if key is None:
                key = SSHKey(user=actor, name=name, public_key=public_key, fingerprint=fingerprint)
            key.enabled = True
            key.full_clean()
            key.save()
            audit(actor, "ssh_key_enable" if restored else "ssh_key_add", key.pk, fingerprint=fingerprint)
        return result(complete=True, notices=[("success", tr("Public key enabled. Running environments are being updated.") if restored else tr("Public key added. Running environments are being updated."))])
    return result({"keys": SSHKey.objects.filter(user=actor).order_by("pk")})


@authenticated
def delete_key(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    key = find(SSHKey, pk=pk, user=actor)
    key.enabled = False
    key.save(update_fields=["enabled"])
    audit(actor, "ssh_key_disable", key.pk)
    notices.append(("success", tr("Public key disabled. Running environments are being updated.")))
    return result(complete=True, notices=notices)


@authenticated
def new_request(actor, mode="interactive", *, values=None, query=None):
    from .catalog import request_options
    from .commands import submit_task

    if values is not None:
        submitted = submit_task(actor, {**values, "mode": mode})
        return result(complete=True, identity=submitted["id"])
    query = parameters(query)
    initial: dict[str, Any] = {}
    node_id = query.get("node")
    if mode == "batch" and query.get("image"):
        image = find(
            ImageArtifact.objects.filter(Q(owner=actor) | Q(shared=True), verified=True), pk=query["image"]
        )
        initial.update(image=str(image.pk), node=image.node_id, source_kind="image")
        node_id = image.node_id
    if mode == "batch" and query.get("source"):
        source = find(ResourceRequest, pk=query["source"], user=actor, mode="interactive", status="running")
        initial.update(
            source_environment=str(source.pk),
            node=source.node_id,
            source_kind="environment",
            purpose=source.purpose,
        )
        node_id = source.node_id
    options = request_options(actor, node_id=node_id, mode=mode)
    options["initial"] = {**options.get("initial", {}), **initial}
    from gpu_platform_control.core.nodes.selection import selected_node
    from gpu_platform_control.services.queries import request_capacity

    node = selected_node(node_id)
    return result(
        {
            "form_options": options,
            "mode": mode,
            "capacity": request_capacity(actor, node=node),
            "title": tr("New development environment") if mode == "interactive" else tr("Jobs"),
        }
    )


@authenticated
def tasks(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    mode = "batch" if query.get("mode") == "batch" else "interactive"
    tasks = history_page(
        query, ResourceRequest.objects.filter(user=actor, mode=mode).select_related("user", "workspace")
    )
    return result({"tasks": tasks, "mode": mode}, notices=notices)


@authenticated
def task(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    query_parameters = query
    query = tasks_for(actor)
    req = find(query.select_related("workspace", "image", "user"), pk=pk)
    ws = getattr(req, "workspace", None)
    from gpu_platform_control.core.services import work_owner

    volume = VolumeAllocation.objects.filter(request=work_owner(req)).first()
    from gpu_platform_control.core.ssh_access import administrator_access

    admin_access = administrator_access(actor)
    managed = bool(
        actor.is_staff
        and ((parameters(query_parameters).get("from") == "management") or req.user_id != actor.pk)
    )
    from gpu_platform_control.core.policies import extension_limit
    from gpu_platform_control.core.services import policy_for

    limit = extension_limit(req.user)
    pending_extension = req.extensions.filter(status="pending").first()
    can_extend = bool(
        req.user_id == actor.pk
        and req.mode == "interactive"
        and req.can_stop
        and ws
        and (not ws.stop_requested)
        and req.ends_at
        and (req.ends_at > timezone.now())
        and (req.extension_count < limit)
        and (not pending_extension)
    )
    from gpu_platform_control.core.features import enabled
    from .resource_display import task_resources
    from .peer_ssh import connection as peer_ssh_connection, peers
    from gpu_platform_control.slots.gateway import Gateway

    internal_ssh = peer_ssh_connection(req) if req.user_id == actor.pk else None
    return result(
        {
            "can_save_environment": enabled(req.node, "save_environment"),
            "task_resources": task_resources(req),
            "task": req,
            "ws": ws,
            "volume": volume,
            "host": config()["platform"]["public_host"],
            "administrator_access": admin_access,
            "internal_ssh": internal_ssh,
            "internal_ssh_peers": peers(req, Gateway()) if internal_ssh else [],
            "admin_connection": bool(actor.is_staff and req.user_id != actor.pk),
            "managed": managed,
            "can_extend": can_extend,
            "extension_limit": limit,
            "extension_hours": None
            if policy_for(req.user).unlimited
            else policy_for(req.user).interactive_hours,
            "pending_extension": pending_extension,
            "extensions": req.extensions.order_by("-created_at")[:5],
        },
        notices=notices,
    )


@authenticated
def request_extension(actor, pk, *, values=None, query=None):
    from gpu_platform_control.core.approvals import request_extension as submit_extension

    find(ResourceRequest, pk=pk, user=actor)
    values = values or {}
    entry = submit_extension(pk, actor, ends_at=values.get("ends_at"), reason=values.get("reason", ""))
    return result(
        complete=True,
        notices=[
            (
                "success",
                tr("Extension approved.")
                if entry.status == "approved"
                else tr("Extension request submitted."),
            )
        ],
    )


@authenticated
def task_status(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    query = tasks_for(actor)
    req = find(query.select_related("workspace", "user"), pk=pk)
    ws = getattr(req, "workspace", None)
    return result(
        {
            "id": str(req.pk),
            "status": req.status,
            "label": req.get_status_display(),
            "login_name": req.login_user,
            "home_dir": req.home_dir,
            "ssh_port": ws.connection.get("port") if ws and req.status == "running" else None,
            "stop_requested": bool(ws and ws.stop_requested),
            "exit_code": ws.exit_code if ws else None,
            "error": execution_error(ws.error, req.node) if ws else "",
        },
        notices=notices,
    )


@authenticated
def stop(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    query = tasks_for(actor)
    req = find(query, pk=pk)
    request_stop(req.pk, actor)
    notices.append(("success", tr("Task stop requested.")))
    return result(complete=True, notices=notices)


@staff_only
def review(actor, pk, *, values=None, query=None):
    values = values or {}
    action = values.get("action")
    reason = values.get("reason", "")
    if action == "approve":
        approve(pk, actor, reason, int(values.get("priority") or 0))
    elif action == "reject":
        reject(pk, actor, reason)
    elif action == "stop":
        request_stop(pk, actor, reason)
    elif action == "extend":
        extend(pk, actor, values.get("ends_at"), reason)
    else:
        raise ValidationError(tr("Select a valid review action."))
    return result(complete=True, notices=[("success", tr("Action recorded."))])


@authenticated
def images(actor, *, values=None, query=None):
    from gpu_platform_control.core.nodes.models import ComputeNode
    from .catalog import build_options
    from .commands import submit_build

    if values is not None:
        submit_build(actor, values)
        return result(complete=True)
    query = parameters(query)
    permanent_images = image_catalog(actor).filter(temporary=False).filter(
        Q(state="ready", verified=True) | Q(owner=actor, state="deleting")
    )
    return result(
        {
            "form_options": build_options(actor),
            "sync_targets": ComputeNode.objects.filter(enabled=True, capabilities__features__image_sync=True),
            "images": history_page(query, permanent_images, "images_page"),
            "temporary_images": history_page(query, image_catalog(actor, "temporary"), "temporary_page"),
            "builds": history_page(
                query,
                ImageBuild.objects.filter(user=actor).order_by("-created_at", "-pk"),
                "builds_page",
                size=10,
            ),
            "snapshots": history_page(
                query,
                EnvironmentSnapshot.objects.filter(source__user=actor)
                .select_related("source", "artifact")
                .order_by("-created_at", "-pk"),
                "snapshots_page",
            ),
        }
    )


@authenticated
def timeline(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from gpu_platform_control.services.timeline import allocation_timeline

    return result(allocation_timeline(actor, query.get("date")), notices=notices)


@authenticated
@transaction.atomic
def save_environment(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    source = find(
        ResourceRequest.objects.exclude(workspace__stop_requested=True),
        pk=pk,
        user=actor,
        mode="interactive",
        status="running",
    )
    from gpu_platform_control.core.features import require

    require(source.node, "save_environment")
    if not source.snapshots.filter(status__in=["pending", "working"]).exists():
        EnvironmentSnapshot.objects.create(source=source)
        audit(actor, "save_environment", source.pk)
        notices.append(("success", tr("Saving environment.")))
    return result(complete=True, notices=notices)


@authenticated
def available_resources(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from gpu_platform_control.services.queries import request_capacity
    from gpu_platform_control.core.nodes.selection import selected_node

    node = selected_node(query.get("node"))
    response = result(request_capacity(actor, node=node), notices=notices)
    return response


@staff_only
def management(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    return result(complete=True, notices=notices)


def health(actor, *, values=None, query=None):
    query = parameters(query)
    values = parameters(values) if values is not None else None
    obs = SystemObservation.objects.filter(name="reconciler").order_by("-observed_at").first()
    ok = bool(obs and obs.observed_at >= timezone.now() - timedelta(seconds=60))
    return result({"web": "ok", "reconciler": "ok" if ok else "stale"})


@authenticated
def api_access(actor, *, values=None, query=None):
    from gpu_platform_control.core.api_auth import issue_token, list_tokens, TOKEN_SECONDS, TOKEN_LIFETIMES

    if values is not None:
        with transaction.atomic():
            credential = issue_token(actor, values.get("expires_in", TOKEN_SECONDS), values.get("name", ""))
            audit(
                actor,
                "api_token_issued",
                credential["id"],
                name=credential["name"],
                expires_in=credential["expires_in"],
            )
        return result(complete=True, notices=[("success", tr("Token created."))])
    return result(
        {
            "tokens": list_tokens(actor, include_secrets=True),
            "api_url": "",
            "form_options": {"initial": {"expires_in": TOKEN_SECONDS}, "lifetimes": TOKEN_LIFETIMES},
        }
    )


@authenticated
@transaction.atomic
def delete_api_token(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from gpu_platform_control.core.api_auth import revoke_token

    if not revoke_token(actor, pk):
        raise NotFound
    audit(actor, "api_token_revoked", pk)
    notices.append(("success", tr("Token deleted.")))
    return result(complete=True, notices=notices)


@authenticated
def synchronization(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from .synchronization import sync_images, sync_jobs
    from gpu_platform_control.core.nodes.models import ComputeNode
    from gpu_platform_control.core.features import enabled

    nodes = list(ComputeNode.objects.filter(enabled=True))

    return result(
        {"jobs": sync_jobs(actor), "nodes": nodes, "section": "syncs",
         "section_title": tr("Synchronization"),
         "images": sync_images(actor),
         "can_sync_images": any(enabled(node, "image_sync") for node in nodes),
         "can_sync_datasets": actor.is_staff and any(enabled(node, "dataset_sync") for node in nodes)},
        notices=notices,
    )


@authenticated
def sync_image(actor, pk, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from .synchronization import request_image_sync
    from gpu_platform_control.core.nodes.models import ComputeNode

    image = find(ImageArtifact, pk=pk)
    target = find(ComputeNode, pk=values.get("target"))
    try:
        request_image_sync(actor, image, target)
    except ValidationError as exc:
        from .errors import message

        notices.append(("error", message(getattr(exc, "code", "") or "sync_failed", target)))
    return result(complete=True, notices=notices)


@staff_only
def sync_datasets(actor, *, values=None, query=None):
    notices: list[tuple[str, object]] = []
    query = parameters(query)
    values = parameters(values) if values is not None else None
    from .synchronization import request_dataset_sync
    from gpu_platform_control.core.nodes.models import ComputeNode

    source = find(ComputeNode, pk=values.get("source"))
    target = find(ComputeNode, pk=values.get("target"))
    paths = [
        value.strip() for value in values.get("paths", "").replace("\n", ",").split(",") if value.strip()
    ]
    try:
        request_dataset_sync(actor, source, target, paths)
    except ValidationError as exc:
        from .errors import message

        notices.append(("error", message(getattr(exc, "code", "") or "sync_failed", target)))
    return result(complete=True, notices=notices)


@authenticated
def notification_preferences(actor, *, values=None, query=None):
    from .notifications import notification_channels, set_notification_receiver

    if values is not None:
        set_notification_receiver(actor, values.get("channel"), values.get("recipient", {}))
        return result(complete=True)
    return result({"channels": notification_channels(actor)})
