"""Durable notification events delivered by registered channel implementations."""

import base64
import hashlib
from datetime import timedelta, timezone as datetime_timezone
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone, translation
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from gpu_platform_contracts.slots.notify import IN_APP_CHANNEL, InboxDelivery
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
    "submit": _("Task request submitted"),
    "approve": _("Task request approved"),
    "reject": _("Task request declined"),
    "extension_submitted": _("Time extension requested"),
    "extension_approved": _("Time extension approved"),
    "extension_rejected": _("Time extension declined"),
    "running": _("Task started"),
    "finished": _("Task ended"),
    "ending_soon": _("Dev environment ending soon"),
    "volume_deletion_notice": _("Work volume scheduled for deletion"),
    "node_offline": _("Server offline"),
    "image_build_succeeded": _("Image build completed"),
    "image_build_failed": _("Image build failed"),
    "environment_saved": _("Dev environment saved as an image"),
    "long_batch": _("Batch job still running"),
    "sync_completed": _("Synchronization completed"),
}

EVENT_MESSAGES = {
    "submit": _("Open the task to check its approval status and scheduled time."),
    "approve": _("The request was approved. Open the task to check when it starts."),
    "reject": _("The task will not start. Review the decision before submitting a new request."),
    "extension_submitted": _("The current end time still applies. Check the task for the decision."),
    "extension_approved": _("The dev environment can run longer. Check the new end time in the task details."),
    "extension_rejected": _("The current end time still applies. Review the decision and save your work before the environment stops."),
    "running": _("The task is running. Open its details to see connection information or check progress."),
    "finished": _("The task has stopped. Open its details to check the final status and results."),
    "ending_soon": _("The dev environment will stop at its scheduled end time. Save your work or request more time before then."),
    "volume_deletion_notice": _("The work volume is nearing the end of its retention period. Copy any files you need before deletion."),
    "node_offline": _("The server stopped reporting. Task status may be out of date. Check again when the server reconnects."),
    "image_build_succeeded": _("The image is available. Select it when creating a dev environment or batch job."),
    "image_build_failed": _("The build did not produce a usable image. Check the build log, correct the error, and submit the build again."),
    "environment_saved": _("The saved image is available. Select it to start another dev environment or batch job."),
    "long_batch": _("The batch job has reached the reminder time and is still using resources. Check its progress and stop it if it has finished its work."),
    "sync_completed": _("The copy is available on the destination server. Open the destination to check the copied data."),
}
MANUAL_NOTICE = "manual_notice"
NODE_OFFLINE_LIFETIME = timedelta(hours=24)
DEFAULT_IN_APP_EVENTS = tuple(code for code in EVENTS if code not in {"submit", "extension_submitted"})


def channel_configured(channel):
    return any(selection.module == channel for selection in cluster_config().control.notify)


def automatic_events(channel, settings):
    if channel == IN_APP_CHANNEL:
        return (settings.receivers.get(channel, {}).get("events", DEFAULT_IN_APP_EVENTS)
                if settings else DEFAULT_IN_APP_EVENTS)
    return settings.events if settings and settings.enabled else ()


def reminder_settings(event):
    settings = NotificationSettings.objects.filter(pk=1).first()
    if any(event in automatic_events(selection.module, settings) for selection in cluster_config().control.notify):
        return settings.reminder_minutes if settings else 30
    return None


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
    configuration = NotificationSettings.objects.filter(pk=1).first()
    selections = [selection for selection in selections if row.action in automatic_events(selection.module, configuration)]
    if not selections:
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
    recipients = [recipient for recipient in recipients if recipient.is_active]
    for selected in selections:
        targets = recipients
        if selected.module == "pushplus":
            # The shared WeChat destination receives one copy for the administrators.
            targets = get_user_model().objects.filter(is_active=True, is_staff=True).order_by("pk")[:1]
        for recipient in targets:
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
    minutes = reminder_settings("ending_soon")
    if minutes is None:
        return
    now = timezone.now()
    requests = ResourceRequest.objects.filter(
        mode="interactive",
        status="running",
        ends_at__gt=now,
        ends_at__lte=now + timedelta(minutes=minutes),
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

    minutes = reminder_settings("volume_deletion_notice")
    if minutes is None:
        return
    now = clock()
    volumes = VolumeAllocation.objects.select_for_update().filter(
        kind="work", state="bound", retain_until__gt=now,
        retain_until__lte=now + timedelta(minutes=minutes),
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

    if reminder_settings("long_batch") is None:
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


def render_event(job, event, loader):
    if event.code == MANUAL_NOTICE:
        return job.title, job.content, {}
    title = str(event_labels(loader).get(event.code, _("Platform notification")))
    parameters = event.parameters
    task = None
    target = parameters.get("target")
    if target and event.code not in {"volume_deletion_notice", "node_offline", "image_build_failed", "image_build_succeeded", "sync_completed"}:
        try:
            task = ResourceRequest.objects.filter(pk=target).only("purpose", "user_id").first()
        except (ValidationError, ValueError):
            pass
    message = EVENT_MESSAGES.get(event.code)
    content = [str(message)] if message else []
    presentation = {}
    if task and (job.recipient.is_staff or task.user_id == job.recipient_id):
        content.append(task.purpose)
        presentation["link"] = f"/tasks/{task.pk}"
    elif isinstance(parameters.get("node"), str):
        content.append(parameters["node"])
    elif isinstance(parameters.get("name"), str):
        content.append(parameters["name"])
    instant_field = {"ending_soon": "ends_at", "volume_deletion_notice": "retain_until"}.get(event.code)
    if instant_field and isinstance(parameters.get(instant_field), str):
        deadline = parse_datetime(parameters[instant_field])
        if deadline and timezone.is_aware(deadline):
            content.append(str(_("Deadline: %(time)s") % {"time": date_format(timezone.localtime(deadline), "SHORT_DATETIME_FORMAT")}))
            presentation["expires_at"] = deadline.astimezone(datetime_timezone.utc).isoformat()
    if event.code in {"reject", "extension_rejected"} and isinstance(parameters.get("reason"), str):
        content.append(parameters["reason"])
    return title, "\n".join(content), presentation


def deliver_one(loader=load, clock=timezone.now, *, in_app=None):
    now = clock()
    with transaction.atomic():
        queue = (
            Notification.objects.select_for_update()
            .filter(
                Q(state="pending") | Q(state="sending", next_attempt_at__lte=now),
                next_attempt_at__lte=now,
                recipient__isnull=False,
            )
            .order_by("created_at", "pk")
        )
        if in_app is True:
            queue = queue.filter(channel=IN_APP_CHANNEL)
        elif in_app is False:
            queue = queue.exclude(channel=IN_APP_CHANNEL)
        job = queue.first()
        if job is None:
            return False
        (job.state, job.attempts, job.next_attempt_at) = (
            "sending",
            job.attempts + 1,
            now + timedelta(minutes=5),
        )
        job.save(update_fields=["state", "attempts", "next_attempt_at"])
    attempt = Notification.objects.filter(pk=job.pk, state="sending", attempts=job.attempts)
    if job.event == "node_offline":
        deadline = job.created_at + NODE_OFFLINE_LIFETIME
        job.payload["expires_at"] = deadline.astimezone(datetime_timezone.utc).isoformat()
    expires_at = job.payload.get("expires_at")
    if (not job.recipient.is_active
            or (job.channel == "pushplus" and not job.recipient.is_staff)
            or (expires_at and parse_datetime(expires_at) <= now)):
        attempt.update(state="skipped")
        return True
    try:
        selected = next((item for item in cluster_config().control.notify if item.module == job.channel))
        module = loader("notify", selected.module, options=selected.options)
        configuration = NotificationSettings.load()
        recipient = dict(configuration.receivers.get(job.channel, {})) if job.channel != IN_APP_CHANNEL else {}
        if job.channel not in {IN_APP_CHANNEL, "pushplus"}:
            recipient.update(job.recipient.profile.notification_receivers.get(job.channel, {}))
        for key, specification in module.recipient_fields().items():
            if specification.get("secret") and recipient.get(key):
                recipient[key] = cipher().decrypt(recipient[key].encode()).decode()
        language = job.recipient.profile.language or cluster_config().cluster.default_language
        event = Message(code=job.payload["code"], parameters=job.payload["parameters"])
        with translation.override(language):
            title, content, presentation = render_event(job, event, loader)
            event = event.model_copy(
                update={"parameters": {**event.parameters, "title": title, "content": content}}
            )
        delivery = module.send(recipient, event, language)
        if isinstance(delivery, InboxDelivery):
            title, content = delivery.title, delivery.content
    except Exception as exc:
        attempt.update(
            state="pending" if job.attempts < 3 else "failed",
            error=type(exc).__name__,
            next_attempt_at=now + timedelta(seconds=60 * 2**job.attempts),
        )
    else:
        attempt.update(state="sent", sent_at=clock(), error="", title=title[:100],
                       content=content, payload={**job.payload, **presentation})
    return True
