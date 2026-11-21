"""JSON API for the same operations and authorization rules as the portal."""

from django.utils.translation import gettext_lazy as tr
from contextlib import nullcontext
from datetime import timedelta
from functools import wraps
import hashlib
import io
import json
import logging
from pathlib import Path
import zipfile
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import PermissionDenied, ValidationError, RequestDataTooBig
from django.core.paginator import EmptyPage, Paginator
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.csrf import csrf_exempt
from gpu_platform_control.core.api_auth import (
    bearer_user,
    issue_token,
    list_tokens,
    revoke_token,
    get_token,
    token_id,
    TOKEN_SECONDS,
)
from gpu_platform_control.core.access import (
    tasks_for,
    images_for,
    image_actions_for,
    volumes_for,
    capabilities,
)
from gpu_platform_control.services.queries import (
    resource_overview,
    node_resource_groups,
    member_list,
    group_list,
    image_catalog,
    managed_image_list,
    image_summary,
    pool_list,
    audit_list,
    container_list,
)
from gpu_platform_control.core.configuration import config
from .errors import execution_error
from .boundary import NotFound
from gpu_platform_control.core.datasets import dataset_download, dataset_content, Download
from gpu_platform_control.core.image_lifecycle import keep_image, recycle_image
from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.retention.models import EnvironmentSnapshot, ImageArtifact, ImageBuild, PlatformSettings, VolumeAllocation
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.nodes.models import SystemObservation
from gpu_platform_control.core.accounts.models import UserGroup, GROUP_QUOTA_FIELDS
from gpu_platform_control.core.services import (
    ACTIVE,
    approve,
    audit,
    extend,
    policy_for,
    reject,
    request_stop,
)
from gpu_platform_control.services.timeline import allocation_timeline
from gpu_platform_control.core.volumes import annotate_volume_usage, request_volume_deletion, volume_busy

from . import commands
from .projections import gpu_request_limits, group_fields, policy_fields, task_fields, task_input, quota_input, RESOURCE_FIELDS, ALLOCATION_FIELDS

log = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, code, message, status=400, fields=None):
        (self.code, self.message, self.status, self.fields) = (code, message, status, fields)


def success(data, status=200):
    response = JsonResponse({"data": data}, status=status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    return response


def endpoint(*methods, staff=False, public=(), idempotent=False, authorize=None):

    def decorate(func):

        @csrf_exempt
        @wraps(func)
        def view(request, *args, **kwargs):
            try:
                if request.method not in methods:
                    raise APIError("method_not_allowed", tr("This request method is unavailable."), 405)
                if request.method not in public:
                    user = bearer_user(request)
                    if user is None:
                        raise APIError("unauthorized", tr("Provide a valid Bearer token."), 401)
                    request.user = user
                    if staff and (not user.is_staff):
                        raise PermissionDenied
                request.api_data = {}
                if request.body:
                    if request.content_type != "application/json":
                        raise APIError(
                            "unsupported_media_type", tr("Use application/json for the request body."), 415
                        )
                    try:
                        data = json.loads(request.body)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise APIError("invalid_json", tr("The JSON body is invalid.")) from exc
                    if not isinstance(data, dict):
                        raise APIError("invalid_json", tr("The JSON body must be an object."))
                    request.api_data = data
                write = request.method not in {"GET", "HEAD"} and request.method not in public
                with transaction.atomic() if write else nullcontext():
                    if authorize:
                        authorize(request, *args, **kwargs)
                    key = request.headers.get("Idempotency-Key", "") if idempotent and write else ""
                    if key:
                        if len(key) > 128 or not key.isascii() or any((ord(c) < 33 for c in key)):
                            raise APIError(
                                "invalid_idempotency_key",
                                tr("Idempotency-Key must contain 1–128 visible ASCII characters."),
                            )
                        target = hashlib.sha256(
                            (request.method + " " + request.path + " " + key).encode()
                        ).hexdigest()
                        fingerprint = salted_hmac(
                            "platform-api-idempotency",
                            json.dumps(request.api_data, sort_keys=True, ensure_ascii=False),
                        ).hexdigest()
                        previous = AuditLog.objects.filter(
                            actor=request.user, action="api_idempotency", target=target
                        ).first()
                        if previous:
                            if previous.detail["fingerprint"] != fingerprint:
                                raise APIError(
                                    "idempotency_conflict",
                                    tr("This Idempotency-Key was used with different parameters."),
                                    409,
                                )
                            response = success(previous.detail["data"], previous.detail["status"])
                            response["Idempotency-Replayed"] = "true"
                            return response
                    response = func(request, *args, **kwargs)
                    if key and isinstance(response, JsonResponse) and (response.status_code < 400):
                        audit(
                            request.user,
                            "api_idempotency",
                            target,
                            fingerprint=fingerprint,
                            data=json.loads(response.content)["data"],
                            status=response.status_code,
                        )
                    return response
            except APIError as exc:
                error = {"code": exc.code, "message": exc.message}
                if exc.fields is not None:
                    error["fields"] = exc.fields
                response = JsonResponse(
                    {"error": error}, status=exc.status, json_dumps_params={"ensure_ascii": False}
                )
                response["Cache-Control"] = "no-store"
                if exc.status == 401:
                    response["WWW-Authenticate"] = "Bearer"
                if exc.status == 405:
                    response["Allow"] = ", ".join(methods)
                return response
            except ValidationError as exc:
                return JsonResponse(
                    {"error": {"code": getattr(exc, "code", None) or "validation_error",
                               "message": "; ".join(exc.messages)}}, status=400
                )
            except PermissionDenied:
                return JsonResponse(
                    {
                        "error": {
                            "code": "forbidden",
                            "message": tr("This operation requires additional permissions."),
                        }
                    },
                    status=403,
                )
            except (Http404, NotFound):
                return JsonResponse(
                    {"error": {"code": "not_found", "message": tr("The requested item is unavailable.")}},
                    status=404,
                )
            except IntegrityError:
                return JsonResponse(
                    {
                        "error": {
                            "code": "conflict",
                            "message": tr("The request conflicts with an existing record."),
                        }
                    },
                    status=409,
                )
            except RequestDataTooBig:
                return JsonResponse(
                    {
                        "error": {
                            "code": "payload_too_large",
                            "message": tr("The request body exceeds the size limit."),
                        }
                    },
                    status=413,
                )
            except Exception:
                log.exception("API operation failed: %s %s", request.method, request.path)
                return JsonResponse(
                    {
                        "error": {
                            "code": "server_error",
                            "message": tr("The operation failed. Check its status before retrying."),
                        }
                    },
                    status=500,
                )

        (view.api_methods, view.api_staff, view.api_public, view.api_idempotent) = (
            methods,
            staff,
            public,
            idempotent,
        )
        return view

    return decorate


def body(request, allowed):
    data = request.api_data.copy()
    unknown = set(data) - set(allowed)
    if unknown:
        raise APIError("unknown_fields", tr("Unknown fields: ") + ", ".join(sorted(unknown)))
    return data


def number(value, default, minimum=1, maximum=1000000):
    try:
        result = int(value) if value is not None else default
    except (ValueError, TypeError) as exc:
        raise APIError("invalid_number", tr("The parameter must be an integer.")) from exc
    if not minimum <= result <= maximum:
        raise APIError(
            "invalid_number",
            tr("The parameter must be between {value_0} and {value_1}.").format(
                value_0=minimum, value_1=maximum
            ),
        )
    return result


def page(request, query, serialize, prepare=None, extra=None):
    size = number(request.GET.get("page_size"), 20, maximum=100)
    index = number(request.GET.get("page"), 1)
    paginator = Paginator(query, size)
    try:
        rows = paginator.page(index)
    except EmptyPage:
        return success(
            {
                "items": [],
                "page": index,
                "page_size": size,
                "count": paginator.count,
                "next_page": None,
                **(extra or {}),
            }
        )
    if prepare:
        rows.object_list = list(rows.object_list)
        prepare(rows.object_list)
    return success(
        {
            "items": [serialize(row) for row in rows],
            "page": index,
            "page_size": size,
            "count": paginator.count,
            "next_page": index + 1 if rows.has_next() else None,
            **(extra or {}),
        }
    )


def owned(request, model, pk, relation="user"):
    query = model.objects.all()
    query = query.filter(**{relation: request.user})
    return get_object_or_404(query, pk=pk)


def node_filter(request, query, field="node"):
    if request.GET.get("node"):
        from gpu_platform_control.core.nodes.selection import selected_node

        query = query.filter(**{field: selected_node(request.GET["node"])})
    return query


def group_data(group, members=False):
    fields = [
        "id",
        "name",
        "is_default",
        "home_size_gib",
        "unlimited",
        "extension_limit",
        "approval_mode",
        *GROUP_QUOTA_FIELDS,
    ]
    data = {name: getattr(group, name) for name in fields}
    data.update(group_fields(group))
    if group.unlimited:
        for name in GROUP_QUOTA_FIELDS:
            data.pop(name)
    if members:
        count = getattr(group, "member_count", None)
        data["member_count"] = group.members.count() if count is None else count
    return data


def user_data(user):
    from gpu_platform_control.core.policies import approval_mode, extension_limit

    policy = policy_for(user)
    effective = group_data(user.profile.group)
    for field in [*RESOURCE_FIELDS, *ALLOCATION_FIELDS]:
        effective.pop(field, None)
    effective.update(policy_fields(policy))
    effective.update(
        home_size_gib=user.profile.home_size_gib,
        unlimited=policy.unlimited,
        concurrent_tasks=policy.concurrent_tasks,
        interactive_hours=policy.interactive_hours,
        extension_limit=policy.extension_limit,
        approval_mode=policy.approval_mode,
        extensions={"resources": policy.resources, "per_allocation": policy.per_allocation},
    )
    return {
        "id": user.pk,
        "username": user.username,
        "first_name": user.first_name,
        "email": user.email,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "group": group_data(user.profile.group),
        "home_quota_gib": user.profile.home_size_gib,
        "resource_limits": user.profile.resource_limits,
        "effective_limits": effective,
        "extension_limit": extension_limit(user),
        "approval_mode": approval_mode(user),
    }


def connection_data(req):
    from .connections import connection_for

    return connection_for(req)


def task_data(req, detail=True):
    fields = [
        "id",
        "purpose",
        "mode",
        "status",
        "starts_at",
        "ends_at",
        "created_at",
        "working_dir",
        "priority",
    ]
    if detail:
        fields += ["command", "environment", "notes", "review_reason"]
    data = {name: getattr(req, name) for name in fields}
    data.update(task_fields(req))
    data["extensions"] = {"resources": req.resource_quantities}
    ws = getattr(req, "workspace", None)
    data.update(
        node_id=req.node_id,
        node_name=req.node.name,
        user_id=req.user_id,
        username=req.user.username,
        image_id=req.image_id,
        image_name=req.image.name,
        extension_count=req.extension_count,
        source_environment_id=req.source_environment_id,
        login_name=req.login_user,
        home_dir=req.home_dir,
        status_label=req.get_status_display(),
        connection=connection_data(req),
        stop_requested=bool(ws and ws.stop_requested),
        started_at=ws.started_at if ws else None,
        ended_at=ws.ended_at if ws else None,
        exit_code=ws.exit_code if ws else None,
        error=execution_error(ws.error, req.node) if ws else "",
        cleanup_complete=bool(ws and ws.cleanup_complete),
        gpu_allocations=ws.allocations.get("gpu", []) if ws else [],
        gpu_compute_total=task_fields(req)["gpu_compute_total"],
        gpu_memory_total_gib=task_fields(req)["gpu_memory_total_gib"],
    )
    if detail:
        from gpu_platform_control.core.services import work_owner

        owner = work_owner(req)
        volume = getattr(owner, "volumeallocation", None)
        data.update(
            source_environment_name=req.source_environment.purpose if req.source_environment_id else None,
            volume=volume_data(volume) if volume else None,
            log_directory="/workspace/.platform/" if req.mode == "batch" else None,
            can_stop=req.can_stop and (not bool(ws and ws.stop_requested)),
        )
    return data


def image_data(image):
    data = {
        name: getattr(image, name)
        for name in [
            "id",
            "name",
            "owner_id",
            "reference",
            "digest",
            "size_bytes",
            "architecture",
            "shared",
            "verified",
            "temporary",
            "state",
            "recycle_after",
            "cleanup_error",
            "created_at",
        ]
    }
    data.update(
        node_id=image.node_id,
        node_name=image.node.name,
        owner_name=image.owner.username if image.owner_id else None,
        state_label=image.get_state_display(),
    )
    return data


def build_data(build, detail=True):
    fields = ["id", "user_id", "name", "context_git", "status", "artifact_id", "error", "created_at"]
    if detail:
        fields += ["dockerfile", "log"]
    return {
        **{name: getattr(build, name) for name in fields},
        "node_id": build.node_id,
        "node_name": build.node.name,
        "status_label": build.get_status_display(),
        "error": execution_error(build.error, build.node),
    }


def snapshot_data(snapshot):
    return {
        **{
            name: getattr(snapshot, name)
            for name in ["id", "source_id", "target_id", "artifact_id", "status", "error", "created_at"]
        },
        "node_id": snapshot.source.node_id,
        "node_name": snapshot.source.node.name,
        "source_name": snapshot.source.purpose,
        "artifact_name": snapshot.artifact.name if snapshot.artifact_id else None,
        "status_label": snapshot.get_status_display(),
        "error": execution_error(snapshot.error, snapshot.source.node),
    }


def volume_data(volume):
    data = {
        name: getattr(volume, name)
        for name in [
            "id",
            "user_id",
            "request_id",
            "kind",
            "size_gib",
            "used_bytes",
            "location",
            "state",
            "retain_until",
            "notified_at",
            "deletion_error",
            "created_at",
        ]
    }
    data.update(
        node_id=volume.node_id,
        node_name=volume.node.name,
        pool_id=volume.pool_id,
        pool_name=volume.pool.name,
        username=volume.user.username,
        request_name=volume.request.purpose if volume.request_id else None,
    )
    data["deletion_error"] = execution_error(volume.deletion_error, volume.node)
    busy = getattr(volume, "busy", None)
    data["busy"] = volume_busy(volume) if busy is None else busy
    return data


@endpoint("GET", public=("GET",))
def index(request):
    return success(
        {
            "version": "v1",
            "schema": "/api/v1/openapi.json",
            "skill": "/api/v1/skill.zip",
            "token_endpoint": "/api/v1/auth/token",
            "timezone": settings.TIME_ZONE,
        }
    )


@endpoint("POST", "DELETE", public=("POST",))
def token(request):
    if request.method == "DELETE":
        identifier = token_id(request.api_session.session_key)
        request.api_session.delete()
        audit(request.user, "api_token_revoked", identifier)
        return success({"revoked": True})
    data = body(request, {"username", "password", "expires_in", "name"})
    if not all((isinstance(data.get(key), str) and data[key] for key in ("username", "password"))):
        raise APIError("invalid_credentials", tr("Provide a username and password."), 401)
    user = authenticate(request, username=data["username"], password=data["password"])
    if user is None:
        raise APIError("invalid_credentials", tr("Invalid username or password."), 401)
    result = issue_token(user, data.get("expires_in", TOKEN_SECONDS), data.get("name", ""))
    audit(user, "api_token_issued", result["id"], name=result["name"], expires_in=result["expires_in"])
    return success(result, 201)


@endpoint("GET", "POST")
def tokens(request):
    if request.method == "POST":
        data = body(request, {"name", "expires_in"})
        result = issue_token(request.user, data.get("expires_in", TOKEN_SECONDS), data.get("name", ""))
        audit(
            request.user,
            "api_token_issued",
            result["id"],
            name=result["name"],
            expires_in=result["expires_in"],
        )
        return success(result, 201)
    return page(
        request, list_tokens(request.user, current_key=request.api_session.session_key), lambda row: row
    )


@endpoint("GET", "DELETE")
def token_entry(request, pk):
    body(request, set())
    if request.method == "GET":
        record = get_token(request.user, pk, current_key=request.api_session.session_key)
        if record is None:
            raise Http404
        return success(record)
    if not revoke_token(request.user, pk):
        raise Http404
    audit(request.user, "api_token_revoked", pk)
    return success({"revoked": True})


@endpoint("GET")
def me(request):
    data = user_data(request.user)
    active = ResourceRequest.objects.filter(user=request.user, status__in=ACTIVE)
    rows = [task_fields(row) for row in active]
    data["usage"] = {key: sum(row[key] for row in rows) for key in ["gpu", "cpu", "memory_gib"]}
    data["usage"].update(active_tasks=len(rows),
        gpu_compute_percent=sum(row["gpu_compute_total"] for row in rows),
        gpu_memory_gib=sum(row["gpu_memory_total_gib"] for row in rows))
    data["permissions"] = capabilities(request.user)
    return success(data)


@endpoint("POST")
def password(request):
    data = body(request, {"old_password", "new_password1", "new_password2"})
    return success(commands.change_password(request.user, **data))


@endpoint("GET")
def resources(request):
    from gpu_platform_control.core.nodes.selection import selected_node

    node = selected_node(request.GET.get("node"))
    from django.db.models import Q
    default = ImageArtifact.objects.filter(node=node, verified=True, state="ready").filter(Q(owner=request.user) | Q(shared=True)).order_by("-shared", "-created_at").first()
    from gpu_platform_control.services.queries import request_capacity

    overview = resource_overview(node if request.GET.get("node") else None)
    return success(
        {
            "now": timezone.now(),
            "timezone": settings.TIME_ZONE,
            **overview,
            "nodes": node_resource_groups(overview, node if request.GET.get("node") else None),
            "request_capacity": request_capacity(request.user, overview, node=node),
            "default_image_id": default.pk if default else None,
            "group": user_data(request.user)["effective_limits"],
        }
    )


@endpoint("GET")
def gpus(request):
    from gpu_platform_control.core.nodes.selection import selected_node

    node = selected_node(request.GET.get("node"))
    overview = resource_overview(node)
    return success(
        {
            "devices": overview["gpus"],
            "fresh": overview["fresh"],
            "request_limits": gpu_request_limits(request.user, node=node),
            "explanation": execution_error(node.capabilities.get("gpu_explanation"), node),
        }
    )


@endpoint("GET")
def timeline(request):
    from gpu_platform_control.core.nodes.selection import selected_node

    node = selected_node(request.GET["node"]) if request.GET.get("node") else None
    return success(allocation_timeline(request.user, request.GET.get("date"), node=node))


@endpoint("GET", "POST", idempotent=True)
def keys(request):
    if request.method == "GET":
        return page(
            request,
            request.user.sshkey_set.order_by("pk"),
            lambda key: {
                name: getattr(key, name)
                for name in ["id", "name", "public_key", "fingerprint", "enabled", "created_at"]
            },
        )
    data = body(request, {"name", "public_key"})
    from gpu_platform_control.core.services import parse_ssh_key
    from gpu_platform_control.core.accounts.models import SSHKey
    public_key, fingerprint = parse_ssh_key(data["public_key"])
    existing = request.user.sshkey_set.filter(fingerprint=fingerprint).first()
    if existing and existing.enabled:
        raise APIError("duplicate_key", tr("This public key is already registered."), 409)
    key = existing or SSHKey(name=data["name"], public_key=public_key, fingerprint=fingerprint)
    (key.user, key.enabled) = (request.user, True)
    key.save()
    audit(request.user, "ssh_key_enable" if existing else "ssh_key_add", key.pk, fingerprint=key.fingerprint)
    return success(
        {
            "id": key.pk,
            "name": key.name,
            "public_key": key.public_key,
            "fingerprint": key.fingerprint,
            "enabled": True,
        },
        201,
    )


@endpoint("PATCH", "DELETE")
def key(request, pk):
    key = get_object_or_404(request.user.sshkey_set, pk=pk)
    data = body(request, {"name", "enabled"})
    if "enabled" in data and (not isinstance(data["enabled"], bool)):
        raise APIError("invalid_field", tr("enabled must be a boolean."))
    if "name" in data:
        key.name = data["name"]
        key.full_clean()
    key.enabled = False if request.method == "DELETE" else data.get("enabled", key.enabled)
    key.save()
    audit(request.user, "ssh_key_enable" if key.enabled else "ssh_key_disable", key.pk)
    return success({"id": key.pk, "name": key.name, "enabled": key.enabled, "applies_to_running_tasks": True})


@endpoint("GET", "POST", idempotent=True)
def tasks(request):
    if request.method == "GET":
        query = (
            tasks_for(request.user, own=request.GET.get("scope") != "all")
            .select_related("workspace", "user", "image")
            .order_by("-created_at")
        )
        if request.GET.get("scope") == "all":
            if not request.user.is_staff:
                raise PermissionDenied
        else:
            query = query.filter(user=request.user)
        query = node_filter(request, query)
        for field in ("mode", "status", "purpose"):
            if request.GET.get(field):
                query = query.filter(**{field: request.GET[field]})
        return page(
            request,
            query.defer("command", "environment", "notes", "review_reason", "argv", "image_reference"),
            lambda req: task_data(req, detail=False),
        )
    data = body(request, {"node", "purpose", "image", "source_environment", "source_kind", "mode", "gpu", "gpu_requests", "cpu", "memory_gib", "workspace_gib", "starts_at", "ends_at", "command", "working_dir", "environment", "notes", "extensions"})
    result = commands.submit_task(request.user, task_input(request.user, data))
    return success(task_data(ResourceRequest.objects.get(pk=result["id"])), 201)


def get_task(request, pk):
    query = tasks_for(request.user).select_related(
        "workspace",
        "user",
        "image",
        "source_environment",
        "volumeallocation__pool",
        "volumeallocation__user",
        "source_environment__volumeallocation__pool",
        "source_environment__volumeallocation__user",
    )
    return get_object_or_404(query, pk=pk)


def authorize_task(request, pk):
    request.api_object = get_task(request, pk)


def authorize_save(request, pk):
    source = get_object_or_404(tasks_for(request.user, own=True), pk=pk)
    from gpu_platform_control.core.features import require

    require(source.node, "save_environment")


def extension_data(entry):
    return {
        name: getattr(entry, name)
        for name in [
            "id",
            "request_id",
            "previous_ends_at",
            "ends_at",
            "reason",
            "status",
            "review_reason",
            "reviewed_by_id",
            "reviewed_at",
            "created_at",
        ]
    }


@endpoint("GET", "POST", idempotent=True)
def extensions(request, pk):
    req = get_object_or_404(tasks_for(request.user, own=request.method == "POST"), pk=pk)
    if request.method == "GET":
        return page(request, req.extensions.order_by("-created_at"), extension_data)
    from gpu_platform_control.core.approvals import request_extension

    data = body(request, {"ends_at", "reason"})
    entry = request_extension(req.pk, request.user, commands.timestamp(data["ends_at"]), data["reason"])
    return success(extension_data(entry), 201)


@endpoint("POST", staff=True, idempotent=True)
def extension_review(request, pk):
    from gpu_platform_control.core.requests.models import ExtensionRequest
    from gpu_platform_control.core.approvals import review_extension

    get_object_or_404(ExtensionRequest, pk=pk)
    data = body(request, {"action", "reason"})
    return success(
        extension_data(
            review_extension(pk, request.user, data["action"], data.get("reason", ""))
        )
    )


@endpoint("GET", staff=True)
def approval_queue(request):
    from gpu_platform_control.core.requests.models import ExtensionRequest

    if request.GET.get("kind", "tasks") == "extensions":
        query = (
            ExtensionRequest.objects.filter(status="pending")
            .select_related("request__user", "request__node")
            .order_by("created_at", "pk")
        )
        return page(request, node_filter(request, query, "request__node"), extension_data)
    query = (
        ResourceRequest.objects.filter(status="submitted")
        .select_related("user", "image", "node", "workspace")
        .order_by("created_at", "pk")
    )
    return page(request, node_filter(request, query), lambda req: task_data(req, detail=False))


@endpoint("GET", "PATCH", staff=True)
def notification_settings(request):
    from gpu_platform_control.core.events.models import NotificationSettings, Notification
    from gpu_platform_control.core.notifications import event_labels
    from .notifications import notification_channels, set_notification_receiver

    obj = NotificationSettings.load()
    if request.method == "PATCH":
        data = {name: getattr(obj, name) for name in ["enabled", "events", "reminder_minutes"]}
        updates = body(request, {"enabled", "events", "reminder_minutes", "receivers"})
        receivers = updates.pop("receivers", {})
        for channel, values in receivers.items():
            set_notification_receiver(request.user, channel, values, defaults=True)
        data.update(updates)
        if set(data["events"]) - set(event_labels()) or not 1 <= data["reminder_minutes"] <= 1440:
            raise ValidationError(tr("Enter 1–1440 minutes."))
        for key, value in data.items():
            setattr(obj, key, value)
        obj.full_clean()
        obj.save()
        audit(request.user, "push_settings_updated", "platform", enabled=obj.enabled, events=obj.events)
    return success(
        {
            "enabled": obj.enabled,
            "channels": notification_channels(request.user, defaults=True),
            "events": obj.events,
            "available_events": event_labels(),
            "reminder_minutes": obj.reminder_minutes,
            "recent": list(
                Notification.objects.order_by("-created_at").values(
                    "id", "event", "title", "state", "attempts", "error", "created_at", "sent_at"
                )[:10]
            ),
        }
    )


@endpoint("GET")
def task(request, pk):
    return success(task_data(get_task(request, pk)))


@endpoint("GET")
def connection(request, pk):
    req = get_task(request, pk)
    from gpu_platform_control.core.ssh_access import administrator_access

    data = {"status": req.status, "connection": connection_data(req)}
    access = administrator_access(request.user)
    if access is not None:
        data["administrator_access"] = access
    return success(data)


@endpoint("GET", staff=True)
def containers(request):
    query = node_filter(
        request,
        container_list(request.GET.get("q", ""), request.GET.get("status", ""), request.GET.get("mode", "")),
    )
    return page(
        request,
        query.defer("command", "environment", "notes", "review_reason", "argv", "image_reference"),
        lambda req: task_data(req, detail=False),
    )


@endpoint("GET")
def logs(request, pk):
    from gpu_platform_contracts.messages import Operation
    from gpu_platform_control.slots.gateway import Gateway

    req = get_task(request, pk)
    tail = number(request.GET.get("tail"), 200, maximum=3000)
    result = Gateway().request(req.node, Operation.TASK_LOGS, task_id=str(req.pk), tail=tail)
    return success(
        {"text": result["logs"], "available": True, "workspace_log_directory": "/workspace/.platform/"}
    )


@endpoint("POST", idempotent=True, authorize=authorize_task)
def stop(request, pk):
    req = request.api_object
    body(request, set())
    request_stop(req.pk, request.user)
    req.refresh_from_db()
    if hasattr(req, "workspace"):
        req.workspace.refresh_from_db()
    return success(task_data(req), 202)


@endpoint("POST", staff=True, idempotent=True, authorize=authorize_task)
def review(request, pk):
    data = {"reason": "", "priority": 0, "ends_at": None, **body(request, {"action", "reason", "priority", "ends_at"})}
    data["ends_at"] = commands.timestamp(data["ends_at"])
    if data["action"] == "approve":
        approve(pk, request.user, data["reason"], int(data["priority"] or 0))
    elif data["action"] == "reject":
        reject(pk, request.user, data["reason"])
    elif data["action"] == "stop":
        request_stop(pk, request.user, data["reason"])
    else:
        if not data["ends_at"]:
            raise APIError("missing_ends_at", tr("An extension requires ends_at."))
        extend(pk, request.user, data["ends_at"], data["reason"])
    return success(task_data(get_task(request, pk)))


@endpoint("POST", idempotent=True, authorize=authorize_save)
def save(request, pk):
    body(request, set())
    source = get_object_or_404(
        ResourceRequest.objects.exclude(workspace__stop_requested=True),
        pk=pk,
        user=request.user,
        mode="interactive",
        status="running",
    )
    snapshot = source.snapshots.filter(status__in=["pending", "working"]).first()
    if snapshot is None:
        snapshot = EnvironmentSnapshot.objects.create(source=source)
        audit(request.user, "save_environment", source.pk)
    return success(snapshot_data(snapshot), 202)


@endpoint("GET")
def snapshots(request):
    query = (
        node_filter(request, EnvironmentSnapshot.objects.filter(source__user=request.user), "source__node")
        .select_related("source__node", "artifact")
        .order_by("-created_at", "-pk")
    )
    return page(request, query, snapshot_data)


@endpoint("GET")
def snapshot(request, pk):
    return success(snapshot_data(owned(request, EnvironmentSnapshot, pk, "source__user")))


def authorize_image(request, pk, action=None):
    query = (
        image_actions_for(request.user)
        if request.method != "GET"
        else images_for(request.user, management=True)
    )
    request.api_object = get_object_or_404(query.select_related("owner"), pk=pk)


@endpoint("GET")
def images(request):
    kind = request.GET.get("kind")
    if kind not in {None, "permanent", "temporary"}:
        raise APIError("invalid_kind", tr("kind must be permanent or temporary."))
    query = node_filter(request, image_catalog(request.user, kind))
    if request.GET.get("available") == "true":
        query = query.filter(verified=True, state="ready")
    return page(request, query, image_data)


@endpoint("GET", "DELETE", idempotent=True, authorize=authorize_image)
def image(request, pk):
    obj = request.api_object
    if request.method == "DELETE":
        recycle_image(obj.pk, request.user)
        obj.refresh_from_db()
    return success(image_data(obj), 202 if request.method == "DELETE" else 200)


@endpoint("POST", idempotent=True, authorize=authorize_image)
def image_action(request, pk, action):
    obj = request.api_object
    body(request, set())
    if action not in {"keep", "recycle"}:
        raise Http404
    (keep_image if action == "keep" else recycle_image)(obj.pk, request.user)
    obj.refresh_from_db()
    return success(image_data(obj))


@endpoint("GET", "POST", idempotent=True)
def builds(request):
    if request.method == "GET":
        return page(
            request,
            node_filter(request, ImageBuild.objects.filter(user=request.user))
            .defer("log", "dockerfile")
            .order_by("-created_at", "-pk"),
            lambda obj: build_data(obj, detail=False),
        )
    data = body(request, {"node", "name", "dockerfile", "context_git"})
    if ImageBuild.objects.filter(user=request.user, status__in=["submitted", "building"]).exists():
        raise APIError("build_in_progress", tr("Wait for your current image build to finish."), 409)
    result = commands.submit_build(request.user, data)
    obj = ImageBuild.objects.get(pk=result["id"])
    return success(build_data(obj), 201)


@endpoint("GET")
def build(request, pk):
    return success(build_data(owned(request, ImageBuild, pk)))


@endpoint("GET")
def volumes(request):
    query = (
        node_filter(request, volumes_for(request.user, own=True))
        .exclude(state="deleted")
        .order_by("-created_at")
    )
    return page(request, query, volume_data, prepare=annotate_volume_usage)


def authorize_volume(request, pk):
    if request.method == "DELETE" and (not request.user.is_staff):
        raise PermissionDenied
    request.api_object = get_object_or_404(volumes_for(request.user), pk=pk)


@endpoint("GET", "DELETE", idempotent=True, authorize=authorize_volume)
def volume(request, pk):
    obj = request.api_object
    if request.method == "DELETE":
        if not request.user.is_staff:
            raise PermissionDenied
        request_volume_deletion(obj.pk, request.user)
        obj.refresh_from_db()
    return success(volume_data(obj), 202 if request.method == "DELETE" else 200)


def download_response(content):
    from django.utils.http import content_disposition_header

    response = StreamingHttpResponse(content.chunks, content_type="application/octet-stream")
    response["Content-Disposition"] = content_disposition_header(True, content.filename)
    response["Content-Length"] = content.size_bytes
    return response


@endpoint("GET")
def datasets(request):
    from gpu_platform_control.core.nodes.selection import selected_node

    entries = dataset_content(
        request.GET.get("path", ""), request.GET.get("q", ""), node=selected_node(request.GET.get("node"))
    )
    if isinstance(entries, Download):
        return download_response(entries)
    return page(request, entries, lambda item: item)


@endpoint("GET")
def download(request):
    from gpu_platform_control.core.nodes.selection import selected_node

    return download_response(
        dataset_download(request.GET.get("path", ""), selected_node(request.GET.get("node")))
    )


@endpoint("GET", "POST", idempotent=True)
def nodes(request):
    from .projections import node_data
    from gpu_platform_control.core.nodes.models import ComputeNode, default_node_id

    default_node_id()
    if request.method == "GET":
        return page(
            request, ComputeNode.objects.order_by("pk"), lambda node: node_data(node, request.user.is_staff)
        )
    if not request.user.is_staff:
        raise PermissionDenied
    data = body(request, {"name", "enabled", "maintenance"})
    data.setdefault("enabled", True)
    result = commands.save_node(request.user, data)
    node = ComputeNode.objects.get(pk=result["id"])
    audit(request.user, "node_created", node.pk, name=node.name)
    return success(node_data(node, True), 201)


@endpoint("GET", "PATCH")
def node(request, pk):
    from .projections import node_data
    from gpu_platform_control.core.nodes.models import ComputeNode

    obj = get_object_or_404(ComputeNode, pk=pk)
    if request.method == "PATCH":
        if not request.user.is_staff:
            raise PermissionDenied
        data = {name: getattr(obj, name) for name in ("name", "enabled", "maintenance")}
        data.update(body(request, {"name", "enabled", "maintenance"}))
        commands.save_node(request.user, data, node_id=obj.pk)
        obj.refresh_from_db()
        audit(request.user, "node_updated", obj.pk, name=obj.name)
    return success(node_data(obj, request.user.is_staff))


@endpoint("GET", staff=True)
def performance(request):
    from gpu_platform_control.core.nodes.selection import selected_node
    from gpu_platform_control.core.performance import performance_nodes

    node = selected_node(request.GET["node"]) if request.GET.get("node") else None
    return success({"nodes": performance_nodes(node), "refresh_seconds": 5})


@endpoint("GET", staff=True)
def overview(request):
    return success(
        {
            "pending": ResourceRequest.objects.filter(status="submitted").count(),
            "active": ResourceRequest.objects.filter(status__in=ACTIVE).count(),
            "members": get_user_model().objects.count(),
            "volumes": VolumeAllocation.objects.exclude(state="deleted").count(),
            "images": ImageArtifact.objects.exclude(state="deleted").count(),
        }
    )


@endpoint("GET", "POST", staff=True, idempotent=True)
def members(request):
    if request.method == "GET":
        query = member_list(request.GET.get("q", ""))
        return page(request, query, user_data)
    allowed = {"username", "first_name", "email", "group", "password", "is_active", "is_staff", "home_quota_gib", "unlimited", "extension_limit", "approval_mode", "extensions", *GROUP_QUOTA_FIELDS, *RESOURCE_FIELDS, *ALLOCATION_FIELDS}
    result = commands.save_member(request.user, quota_input(body(request, allowed)))
    return success(user_data(get_user_model().objects.get(pk=result["id"])), 201)


@endpoint("GET", "PATCH", staff=True)
def member(request, pk):
    obj = get_object_or_404(get_user_model().objects.select_related("profile__group"), pk=pk)
    if obj.is_staff and (not request.user.is_superuser) and (obj.pk != request.user.pk):
        raise PermissionDenied
    if request.method == "PATCH":
        allowed = {"username", "first_name", "email", "group", "password", "is_active", "is_staff", "home_quota_gib", "unlimited", "extension_limit", "approval_mode", "extensions", *GROUP_QUOTA_FIELDS, *RESOURCE_FIELDS, *ALLOCATION_FIELDS}
        commands.save_member(request.user, quota_input(body(request, allowed)), member_id=obj.pk)
        obj.refresh_from_db()
    return success(user_data(obj))


@endpoint("GET", "POST", staff=True, idempotent=True)
def groups(request):
    if request.method == "GET":
        return page(request, group_list(), lambda obj: group_data(obj, members=True))
    allowed = {"name", "unlimited", "extension_limit", "approval_mode", "make_default", "extensions", *GROUP_QUOTA_FIELDS, *RESOURCE_FIELDS, *ALLOCATION_FIELDS}
    result = commands.save_group(request.user, quota_input(body(request, allowed)))
    return success(group_data(UserGroup.objects.get(pk=result["id"]), members=True), 201)


@endpoint("GET", "PATCH", "DELETE", staff=True)
def group(request, pk):
    obj = get_object_or_404(group_list(), pk=pk)
    if request.method == "DELETE":
        return success(commands.delete_group(request.user, obj.pk, body(request, {"destination"}).get("destination")))
    if request.method == "PATCH":
        allowed = {"name", "unlimited", "extension_limit", "approval_mode", "make_default", "extensions", *GROUP_QUOTA_FIELDS, *RESOURCE_FIELDS, *ALLOCATION_FIELDS}
        commands.save_group(request.user, quota_input(body(request, allowed)), group_id=obj.pk)
        obj.refresh_from_db()
    return success(group_data(obj, members=True))


@endpoint("GET", staff=True)
def storage(request):
    from gpu_platform_control.core.nodes.selection import selected_node

    node = selected_node(request.GET["node"]) if request.GET.get("node") else None
    pools = [
        {
            **{
                name: getattr(pool, name)
                for name in [
                    "id",
                    "node_id",
                    "name",
                    "capacity_gib",
                    "free_gib",
                    "available_gib",
                    "reserved_gib",
                    "healthy",
                    "observed_at",
                    "percent",
                ]
            },
            "node_name": pool.node.name,
        }
        for pool in pool_list()
        if node is None or pool.node_id == node.pk
    ]
    query = (
        node_filter(request, volumes_for(request.user))
        .exclude(state="deleted")
        .order_by("user__username", "kind")
    )
    response = page(request, query, volume_data, prepare=annotate_volume_usage)
    payload = json.loads(response.content)["data"]
    return success({"pools": pools, "volumes": payload})


@endpoint("GET", staff=True)
def managed_images(request):
    query = node_filter(request, managed_image_list())
    return page(request, query, image_data, extra={"summary": image_summary(query)})


@endpoint("GET", staff=True)
def audits(request):
    query = audit_list()
    if request.GET.get("action"):
        query = query.filter(action=request.GET["action"])
    from gpu_platform_control.services.audit_display import audit_presentation

    return page(
        request,
        query,
        lambda row: {
            "id": row.pk,
            "actor_id": row.actor_id,
            "actor_name": row.actor.username if row.actor_id else None,
            "action": row.action,
            **audit_presentation(row),
            "target": row.target,
            "detail": row.detail,
            "created_at": row.created_at,
        },
    )


@endpoint("GET", "PATCH", staff=True)
def recycling_settings(request):
    obj = PlatformSettings.load()
    if request.method == "PATCH":
        data = {name: getattr(obj, name) for name in ("workspace_retention_days", "snapshot_hours", "failed_snapshot_hours")}
        data.update(body(request, ("workspace_retention_days", "snapshot_hours", "failed_snapshot_hours")))
        for key, value in data.items():
            if not 1 <= value <= (3650 if key == "workspace_retention_days" else 8760):
                raise ValidationError(tr("Set a resource limit."))
            setattr(obj, key, value)
        obj.full_clean()
        obj.save()
        audit(request.user, "recycle_settings_updated", "platform", **data)
    cfg = config()["platform"]
    return success(
        {
            **{name: getattr(obj, name) for name in ("workspace_retention_days", "snapshot_hours", "failed_snapshot_hours")},
            "public_host": cfg["public_host"],
            "ssh_ports": cfg["ssh_ports"],
            "timezone": settings.TIME_ZONE,
        }
    )


@endpoint("GET", public=("GET",))
def skill_download(request):
    root = Path(settings.PLATFORM_SKILL_DIRECTORY)
    if not (root / "SKILL.md").is_file():
        raise Http404
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and (not path.is_symlink()) and ("__pycache__" not in path.parts):
                archive.write(path, "gpu-platform/" + str(path.relative_to(root)))
    data.seek(0)
    return FileResponse(
        data, as_attachment=True, filename="gpu-platform-skill.zip", content_type="application/zip"
    )


@endpoint("GET", public=("GET",))
def health(request):
    obs = SystemObservation.objects.filter(name="reconciler").order_by("-observed_at").first()
    fresh = bool(obs and obs.observed_at >= timezone.now() - timedelta(seconds=60))
    return success({"web": "ok", "reconciler": "ok" if fresh else "stale"}, 200 if fresh else 503)


@endpoint(
    "GET",
    "POST",
    "PATCH",
    "PUT",
    "DELETE",
    "HEAD",
    "OPTIONS",
    public=("GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"),
)
def not_found(request, unmatched):
    raise Http404
