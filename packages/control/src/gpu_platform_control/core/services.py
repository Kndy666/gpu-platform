from django.utils.translation import gettext_lazy as tr
import base64
import hashlib
import re
from datetime import timedelta
from pathlib import PurePosixPath
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_ssh_public_key
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.nodes.models import StoragePool
from gpu_platform_control.core.retention.models import VolumeAllocation
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.accounts.models import UserProfile

TERMINAL = {"succeeded", "failed", "canceled", "expired", "cleaned", "rejected"}
ACTIVE = {"approved", "scheduled", "preparing", "queued", "starting", "running"}


def work_owner(req):
    return (
        req.source_environment
        if req.source_environment_id and (not getattr(req, "volumeallocation", None))
        else req
    )


def work_volume(req):
    return VolumeAllocation.objects.get(request=work_owner(req))


def volume_in_use(volume, excluding=None):
    from .volumes import related_requests

    return related_requests(volume).filter(status__in=ACTIVE).exclude(pk=excluding).exists()


def audit(actor, action, target, **detail):
    import json
    from django.core.serializers.json import DjangoJSONEncoder

    detail = json.loads(json.dumps(detail, cls=DjangoJSONEncoder))
    row = AuditLog.objects.create(actor=actor, action=action, target=str(target), detail=detail)
    from .notifications import enqueue_event

    enqueue_event(row)
    return row


def parse_ssh_key(value):
    parts = value.strip().split()
    if len(parts) < 2 or parts[0] not in {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
    }:
        raise ValidationError(tr("Submit a public key in OpenSSH format."))
    normalized = " ".join(parts[:2])
    try:
        key = load_ssh_public_key(normalized.encode())
        if isinstance(key, RSAPublicKey) and key.key_size < 3072:
            raise ValueError("RSA key too short")
        raw = base64.b64decode(parts[1], validate=True)
    except (ValueError, TypeError) as e:
        raise ValidationError(tr("Invalid public key. RSA keys require at least 3072 bits.")) from e
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    return (normalized, fingerprint)


def policy_for(user):
    try:
        from .policies import effective_policy

        return effective_policy(user)
    except ObjectDoesNotExist as e:
        raise ValidationError(tr("The user requires a group assignment.")) from e


def pool_available(pool):
    buffer = (pool.capacity_gib * pool.buffer_percent + 99) // 100
    pending = (
        VolumeAllocation.objects.filter(pool=pool, state="reserved").aggregate(n=Sum("size_gib"))["n"] or 0
    )
    return max(0, min(pool.capacity_gib - buffer - pool.reserved_gib, pool.free_gib - buffer - pending))


def validate_request(req):
    from .identity import validate_login_name

    validate_login_name(req.login_user)
    policy = policy_for(req.user)
    node = req.node
    if not node.enabled or node.maintenance:
        raise ValidationError(tr("The selected node has paused admission."))
    if node.state == "offline" or (node.last_seen and node.last_seen < timezone.now() - timedelta(minutes=2)):
        raise ValidationError(tr("The selected node is offline. Admission resumes when it reconnects."))
    if req.image.node_id != req.node_id or req.image.architecture != node.architecture:
        raise ValidationError(tr("Select an image on the target node with a matching architecture."))
    if req.source_environment_id and req.source_environment.node_id != req.node_id:
        raise ValidationError(
            tr("The source environment, image, and work volume must belong to the same node.")
        )
    if not req.user.is_active:
        raise ValidationError(tr("This account is disabled."))
    if not req.image.verified or (req.image.owner_id != req.user_id and (not req.image.shared)):
        raise ValidationError(tr("The image is unavailable."))
    from .quotas.accounting import normalize_request

    normalize_request(req)
    if not req.user.sshkey_set.filter(enabled=True).exists():
        raise ValidationError(tr("Add an SSH public key before submitting a task."))
    if req.mode == "interactive":
        if not req.starts_at or not req.ends_at or req.ends_at <= max(req.starts_at, timezone.now()):
            raise ValidationError(tr("Select valid start and end times."))
        if not policy.unlimited and req.ends_at - req.starts_at > timedelta(hours=policy.interactive_hours):
            raise ValidationError(
                tr("Each environment may run for up to {value_0} hours.").format(
                    value_0=policy.interactive_hours
                )
            )
        (req.argv, req.environment) = ([], {})
    elif req.mode == "batch":
        req.ends_at = None
        if req.source_environment_id:
            source = req.source_environment
            if (
                source.user_id != req.user_id
                or source.mode != "interactive"
                or source.status != "running"
                or (getattr(source, "workspace", None) and source.workspace.stop_requested)
            ):
                raise ValidationError(tr("Select one of your running development environments."))
            if source.snapshots.filter(status__in=["pending", "working"]).exists():
                raise ValidationError(tr("The environment is being saved. Submit after the save completes."))
        if (
            not isinstance(req.argv, list)
            or not req.argv
            or len(req.argv) > 256
            or (not all((isinstance(v, str) and "\x00" not in v for v in req.argv)))
        ):
            raise ValidationError(tr("Enter a command to run."))
        if not isinstance(req.environment, dict) or len(req.environment) > 64:
            raise ValidationError(tr("Environment variables must be a JSON object with up to 64 entries."))
        for k, v in req.environment.items():
            if not re.fullmatch("[A-Za-z_][A-Za-z0-9_]*", k) or not isinstance(v, str) or "\x00" in v:
                raise ValidationError(tr("Invalid environment variable format."))
            if k.startswith(("PLATFORM_", "TASK_", "LD_", "SSH_", "NVIDIA_", "CUDA_MPS_")) or k in {
                "CUDA_VISIBLE_DEVICES",
                "HOME",
                "USER",
                "LOGNAME",
                "PATH",
                "PYTHONPATH",
                "PYTHONHOME",
                "NSS_WRAPPER_PASSWD",
                "NSS_WRAPPER_GROUP",
            }:
                raise ValidationError(
                    tr("This environment variable is reserved: {value_0}").format(value_0=k)
                )
    else:
        raise ValidationError(tr("Invalid execution mode."))
    p = PurePosixPath(req.working_dir)
    if (
        not p.is_absolute()
        or ".." in p.parts
        or (
            not any(
                (
                    p == PurePosixPath(root) or PurePosixPath(root) in p.parents
                    for root in ("/workspace", req.home_dir)
                )
            )
        )
    ):
        raise ValidationError(
            tr("The working directory must be within /workspace or {value_0}.").format(value_0=req.home_dir)
        )


def reserve_volume(user, size, kind, req=None, node=None):
    now = timezone.now()
    from gpu_platform_control.core.nodes.models import default_node_id

    node_id = req.node_id if req else node.pk if node else default_node_id()
    for pool in StoragePool.objects.filter(
        node_id=node_id, healthy=True, observed_at__gte=now - timedelta(minutes=2)
    ).order_by("-free_gib", "name"):
        available = pool_available(pool)
        if size <= available:
            updated = StoragePool.objects.filter(
                pk=pool.pk, reserved_gib=pool.reserved_gib, healthy=True
            ).update(reserved_gib=F("reserved_gib") + size)
            if updated:
                return VolumeAllocation.objects.create(
                    node_id=node_id,
                    user=user,
                    request=req,
                    kind=kind,
                    pool=pool,
                    size_gib=size,
                )
    raise ValidationError(tr("Storage capacity is insufficient for this volume."))


def home_resize_delta(user, size, node=None):
    query = (
        VolumeAllocation.objects.filter(user=user, kind="home")
        .exclude(state="deleted")
        .select_related("pool")
    )
    volume = (query.filter(node=node) if node else query).first()
    if volume and volume.state == "deleting":
        raise ValidationError(tr("The home volume is being deleted. Wait for deletion to finish."))
    if size < 1:
        raise ValidationError(tr("Volume capacity must be positive."))
    if not volume:
        return (None, 0)
    delta = size - volume.size_gib
    if delta < 0:
        raise ValidationError(tr("Keep or increase the home volume capacity."))
    if delta:
        pool = volume.pool
        available = pool_available(pool)
        if (
            not pool.healthy
            or not pool.observed_at
            or pool.observed_at < timezone.now() - timedelta(minutes=2)
            or (delta > available)
        ):
            raise ValidationError(tr("The home volume storage pool has insufficient capacity."))
    return (volume, delta)


@transaction.atomic
def resize_home(user, size, actor):
    if not actor.is_staff:
        raise ValidationError(tr("Administrator permission is required."))
    UserProfile.objects.select_for_update().get(user=user)
    homes = list(
        VolumeAllocation.objects.select_for_update()
        .filter(user=user, kind="home")
        .exclude(state="deleted")
        .select_related("node")
    )
    changes = (
        [home_resize_delta(user, size, volume.node) for volume in homes]
        if homes
        else [home_resize_delta(user, size)]
    )
    for volume, delta in changes:
        if delta:
            StoragePool.objects.filter(pk=volume.pool_id).update(reserved_gib=F("reserved_gib") + delta)
            volume.size_gib = size
            volume.save(update_fields=["size_gib"])
            audit(actor, "home_resize", user.pk, size_gib=size, node=volume.node_id)
    user.profile.home_size_gib = size
    user.profile.save(update_fields=["home_size_gib"])


@transaction.atomic
def approve(request_id, actor, reason="", priority=None):
    if actor is not None and (not actor.is_staff):
        raise ValidationError(tr("Administrator permission is required."))
    req = ResourceRequest.objects.select_related("user__profile", "image").get(pk=request_id)
    UserProfile.objects.select_for_update().get(user=req.user)
    req = (
        ResourceRequest.objects.select_for_update()
        .select_related("user__profile", "image")
        .get(pk=request_id)
    )
    if actor is None:
        from .policies import approval_mode

        if approval_mode(req.user) != "automatic":
            raise ValidationError(tr("This member requires manual approval."))
    if req.status in ACTIVE or req.status in TERMINAL:
        return req
    if not req.login_name:
        req.login_name = req.source_environment.login_user if req.source_environment_id else req.user.username
    validate_request(req)
    source_volume = work_volume(req.source_environment) if req.source_environment_id else None
    home = (
        VolumeAllocation.objects.filter(node=req.node, user=req.user, kind="home")
        .exclude(state="deleted")
        .first()
    )
    if home and home.state == "deleting":
        raise ValidationError(tr("The home volume is being deleted. Wait for deletion to finish."))
    if not home:
        reserve_volume(req.user, req.user.profile.home_size_gib, "home", node=req.node)
    if source_volume is not None:
        volume = source_volume
        if volume.state in {"deleted", "deleting"}:
            raise ValidationError(tr("The source work volume is unavailable."))
        volume.retain_until = volume.notified_at = None
        volume.save(update_fields=["retain_until", "notified_at"])
        if req.volume_sizes["work"] != volume.size_gib:
            replacement = reserve_volume(req.user, req.volume_sizes["work"], "work", req)
            replacement.state = "copy_pending"
            replacement.save(update_fields=["state"])
    else:
        reserve_volume(req.user, req.volume_sizes["work"], "work", req)
    Workspace.objects.get_or_create(request=req)
    (req.status, req.reviewed_by, req.review_reason, req.reviewed_at) = (
        "approved",
        actor,
        reason,
        timezone.now(),
    )
    if req.mode == "interactive" and req.starts_at > timezone.now():
        req.status = "scheduled"
    if req.source_environment_id:
        from gpu_platform_control.core.retention.models import EnvironmentSnapshot

        EnvironmentSnapshot.objects.create(source=req.source_environment, target=req)
        req.status = "preparing"
    req.image_reference = req.image.reference.split("@")[0] + "@" + req.image.digest
    if not re.fullmatch("sha256:[0-9a-f]{64}", req.image.digest):
        raise ValidationError(tr("The image requires a verified digest."))
    if priority is not None:
        req.priority = priority
    req.save()
    audit(actor, "approve", req.pk, reason=reason, priority=req.priority, automatic=actor is None)
    return req


@transaction.atomic
def reject(request_id, actor, reason):
    if not actor.is_staff or not reason.strip():
        raise ValidationError(tr("Rejection requires an administrator and a reason."))
    req = ResourceRequest.objects.get(pk=request_id)
    if req.status != "submitted":
        raise ValidationError(tr("Only pending requests can be rejected."))
    (req.status, req.reviewed_by, req.review_reason, req.reviewed_at) = (
        "rejected",
        actor,
        reason,
        timezone.now(),
    )
    req.save()
    audit(actor, "reject", req.pk, reason=reason)


@transaction.atomic
def request_stop(request_id, actor, reason="user_requested"):
    req = ResourceRequest.objects.select_for_update().get(pk=request_id)
    if actor.pk != req.user_id and (not actor.is_staff):
        raise ValidationError(tr("Permission denied."))
    if req.status in TERMINAL:
        return
    if req.status == "submitted":
        req.status = "canceled"
        req.save(update_fields=["status"])
    else:
        Workspace.objects.filter(request=req).update(stop_requested=True)
    audit(actor, "stop_requested", req.pk, reason=reason)


def can_adjust_time(req, ws, now):
    return bool(
        req.mode == "interactive"
        and req.status in ACTIVE
        and ws
        and not ws.stop_requested
        and not ws.ended_at
        and req.ends_at
        and req.ends_at > now
    )


@transaction.atomic
def extend(request_id, actor, ends_at, reason, *, automatic=False):
    if (actor is None and not automatic) or (actor is not None and not actor.is_staff):
        raise ValidationError(tr("Administrator permission is required."))
    req = ResourceRequest.objects.select_for_update().get(pk=request_id)
    ws, _ = _lock_adjustable_workspace(req)
    if not ends_at or ends_at <= req.ends_at or not (reason or "").strip():
        raise ValidationError(tr("Choose a later end time and provide a reason."))
    _save_end_time(req, ws, actor, ends_at, reason, action="extend")


@transaction.atomic
def adjust_end_time(request_id, actor, ends_at, reason="", *, by_owner=False):
    req = ResourceRequest.objects.select_for_update().get(pk=request_id)
    if not actor or not actor.is_active:
        raise PermissionDenied
    authorized = req.user_id == actor.pk if by_owner else actor.is_staff
    if not authorized:
        raise PermissionDenied
    ws, now = _lock_adjustable_workspace(req)
    if by_owner and (not ends_at or ends_at >= req.ends_at):
        raise ValidationError({"ends_at": tr("Choose an end time earlier than the current end time.")})
    if not ends_at or ends_at <= max(req.starts_at, now) or ends_at == req.ends_at:
        raise ValidationError(
            {"ends_at": tr("Choose a different end time after the start time and the current time.")}
        )
    if not by_owner and not (reason or "").strip():
        raise ValidationError({"reason": tr("Enter a reason for the time adjustment.")})
    _save_end_time(req, ws, actor, ends_at, reason, action="adjust_time")
    return req


def _lock_adjustable_workspace(req):
    ws = Workspace.objects.select_for_update().filter(request=req).first()
    now = timezone.now()
    if not can_adjust_time(req, ws, now):
        raise ValidationError(tr("Time adjustments require an active development environment."))
    return ws, now


def _save_end_time(req, ws, actor, ends_at, reason, *, action):
    expiry = ends_at.isoformat()
    previous_ends_at = req.ends_at
    workspace_fields = []
    if ws.dispatch_started_at:
        from gpu_platform_contracts.messages import Operation
        from gpu_platform_control.slots.gateway import Gateway

        try:
            Gateway().request(req.node, Operation.TASK_EXTEND, task_id=str(req.pk), expires_at=expiry)
        except Exception as exc:
            raise ValidationError(
                tr("The node could not confirm the new expiry."), code="extension_unconfirmed"
            ) from exc
        if ws.dispatch_payload:
            ws.dispatch_payload["expires_at"] = expiry
            workspace_fields.append("dispatch_payload")
    if ws.expire_at:
        ws.expire_at = ends_at
        workspace_fields.append("expire_at")
    if workspace_fields:
        ws.save(update_fields=workspace_fields)
    req.ends_at = ends_at
    fields = ["ends_at"]
    if ends_at > previous_ends_at:
        req.extension_count += 1
        fields.append("extension_count")
    req.save(update_fields=fields)
    detail = {"ends_at": expiry, "reason": reason}
    if action == "adjust_time":
        detail["previous_ends_at"] = previous_ends_at.isoformat()
    audit(actor, action, req.pk, **detail)
