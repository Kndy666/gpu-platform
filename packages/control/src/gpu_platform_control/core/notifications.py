"""Durable notification events delivered by registered channel implementations."""

import base64
import hashlib
from datetime import timedelta
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from gpu_platform_contracts.texts import Message
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_control.slots import load
from gpu_platform_control.slots.configuration import cluster_config
from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.events.models import Notification, NotificationSettings
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.retention.models import VolumeAllocation

EVENTS = {
    "submit": _("Request submitted"),
    "approve": _("Request approved"),
    "reject": _("Request rejected"),
    "extension_submitted": _("Extension requested"),
    "extension_approved": _("Extension approved"),
    "extension_rejected": _("Extension rejected"),
    "running": _("Task started"),
    "finished": _("Task ended"),
    "ending_soon": _("Environment expiring"),
    "volume_deletion_notice": _("Work volume expiring"),
    "node_offline": _("Node offline"),
    "image_build_succeeded": _("Image build completed"),
    "image_build_failed": _("Image build failed"),
    "environment_saved": _("Environment saved"),
    "long_batch": _("Long-running program"),
    "sync_completed": _("Synchronization completed"),
}


def event_labels(loader=load):
    result = dict(EVENTS)
    from django.utils.translation import get_language

    for selection in cluster_config().control.notify:
        module = loader("notify", selection.module, options=selection.options)
        result.update(
            {code: label.resolve(get_language() or "en") for code, label in module.descriptor.events.items()}
        )
    for capabilities in ComputeNode.objects.values_list("capabilities", flat=True):
        for value in capabilities.get("modules", []):
            descriptor = ModuleDescriptor.model_validate(value)
            result.update(
                {code: label.resolve(get_language() or "en") for code, label in descriptor.events.items()}
            )
    return result


def cipher():
    key = hashlib.sha256(("gpu-platform-push-v1:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encode_token(token):
    return cipher().encrypt(token.encode()).decode()


def enqueue_event(row, *, recipients=None):
    selections = cluster_config().control.notify
    if not selections:
        return
    configuration = NotificationSettings.objects.filter(pk=1, enabled=True).first()
    if not configuration or row.action not in configuration.events:
        return
    if recipients is not None:
        pass
    elif row.action == "node_offline":
        from .services import ACTIVE

        affected = ResourceRequest.objects.filter(node_id=row.target, status__in=ACTIVE).values("user_id")
        recipients = get_user_model().objects.filter(is_active=True).filter(
            Q(is_staff=True) | Q(pk__in=affected)
        )
    else:
        model = VolumeAllocation if row.action == "volume_deletion_notice" else ResourceRequest
        try:
            resource = model.objects.select_related("user").filter(pk=row.target).first()
        except (ValueError, ValidationError):
            resource = None
        recipient = resource.user if resource else row.actor
        recipients = [recipient] if recipient else []
    for recipient in recipients:
        for selected in selections:
            Notification.objects.get_or_create(
                event_key=f"audit:{row.pk}:{selected.module}:{recipient.pk}",
                defaults={
                    "event": row.action,
                    "recipient": recipient,
                    "channel": selected.module,
                    "payload": {"code": row.action, "parameters": {"target": row.target, **row.detail}},
                },
            )


@transaction.atomic
def mark_node_offline(node_id):
    from .services import audit

    node = ComputeNode.objects.select_for_update().get(pk=node_id)
    changed = node.state != "offline"
    node.state, node.error = "offline", "node_unavailable"
    node.save(update_fields=["state", "error"])
    if changed:
        audit(None, "node_offline", node.pk, node=node.name)


def collect_expiry_reminders():
    configuration = NotificationSettings.objects.filter(pk=1, enabled=True).first()
    if not configuration or "ending_soon" not in configuration.events:
        return
    now = timezone.now()
    requests = ResourceRequest.objects.filter(
        mode="interactive",
        status="running",
        ends_at__gt=now,
        ends_at__lte=now + timedelta(minutes=configuration.reminder_minutes),
        workspace__stop_requested=False,
    )
    from gpu_platform_control.core.audit.models import AuditLog
    from .services import audit

    for request in requests:
        end = request.ends_at.isoformat()
        if not AuditLog.objects.filter(
            action="ending_soon", target=str(request.pk), detail__ends_at=end
        ).exists():
            audit(None, "ending_soon", request.pk, ends_at=end)


@transaction.atomic
def collect_retention_reminders(clock=timezone.now):
    from .services import audit
    from .volumes import volume_busy

    configuration = NotificationSettings.objects.filter(pk=1, enabled=True).first()
    if not configuration or "volume_deletion_notice" not in configuration.events:
        return
    now = clock()
    volumes = VolumeAllocation.objects.select_for_update().filter(
        kind="work", state="bound", retain_until__gt=now,
        retain_until__lte=now + timedelta(minutes=configuration.reminder_minutes),
    )
    for volume in volumes:
        deadline = volume.retain_until.isoformat()
        if volume_busy(volume) or AuditLog.objects.filter(
            action="volume_deletion_notice", target=str(volume.pk), detail__retain_until=deadline
        ).exists():
            continue
        audit(None, "volume_deletion_notice", volume.pk, retain_until=deadline, node=volume.node.name)
        volume.notified_at = now
        volume.save(update_fields=["notified_at"])


@transaction.atomic
def collect_long_batch_reminders(clock=timezone.now):
    from .services import audit
    from .configuration import config

    configuration = NotificationSettings.objects.filter(pk=1, enabled=True).first()
    if not configuration or "long_batch" not in configuration.events:
        return
    threshold = timedelta(hours=config()["platform"]["batch_reminder_hours"])
    requests = ResourceRequest.objects.select_for_update().filter(
        mode="batch", status="running", workspace__started_at__lte=clock() - threshold,
        workspace__ended_at__isnull=True, workspace__stop_requested=False,
    ).select_related("workspace")
    for request in requests:
        started = request.workspace.started_at.isoformat()
        if not AuditLog.objects.filter(
            action="long_batch", target=str(request.pk), detail__started_at=started
        ).exists():
            audit(None, "long_batch", request.pk, started_at=started)


def collect_reminders():
    collect_expiry_reminders()
    collect_retention_reminders()
    collect_long_batch_reminders()


def deliver_one(loader=load, clock=timezone.now):
    now = clock()
    with transaction.atomic():
        job = (
            Notification.objects.select_for_update()
            .filter(
                Q(state="pending") | Q(state="sending", next_attempt_at__lte=now),
                next_attempt_at__lte=now,
                recipient__is_active=True,
            )
            .order_by("created_at", "pk")
            .first()
        )
        if job is None:
            return False
        (job.state, job.attempts, job.next_attempt_at) = (
            "sending",
            job.attempts + 1,
            now + timedelta(minutes=5),
        )
        job.save(update_fields=["state", "attempts", "next_attempt_at"])
    try:
        selected = next((item for item in cluster_config().control.notify if item.module == job.channel))
        module = loader("notify", selected.module, options=selected.options)
        configuration = NotificationSettings.load()
        recipient = {
            **configuration.receivers.get(job.channel, {}),
            **job.recipient.profile.notification_receivers.get(job.channel, {}),
        }
        for key, specification in module.recipient_fields().items():
            if specification.get("secret") and recipient.get(key):
                recipient[key] = cipher().decrypt(recipient[key].encode()).decode()
        language = job.recipient.profile.language or cluster_config().cluster.default_language
        event = Message.model_validate(job.payload)
        with translation.override(language):
            title = str(event_labels(loader).get(event.code, event.code))
            content = "\n".join((f"{key}: {value}" for (key, value) in event.parameters.items()))
            event = event.model_copy(
                update={"parameters": {**event.parameters, "title": title, "content": content}}
            )
        module.send(recipient, event, language)
    except Exception as exc:
        Notification.objects.filter(pk=job.pk).update(
            state="pending" if job.attempts < 3 else "failed",
            error=type(exc).__name__,
            next_attempt_at=now + timedelta(seconds=60 * 2**job.attempts),
        )
    else:
        Notification.objects.filter(pk=job.pk).update(state="sent", sent_at=clock(), error="")
    return True
