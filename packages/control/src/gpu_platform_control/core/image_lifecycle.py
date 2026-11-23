from django.utils.translation import gettext_lazy as tr
import logging
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from gpu_platform_control.core.retention.models import ImageArtifact, PlatformSettings
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.sync.models import SyncJob
from .services import TERMINAL, audit

log = logging.getLogger(__name__)


def in_use(image):
    return (
        ResourceRequest.objects.filter(image=image).exclude(status__in=TERMINAL).exists()
        or Workspace.objects.filter(request__image=image, cleanup_complete=False).exists()
        or SyncJob.objects.filter(image=image, state__in=["queued", "running"]).exists()
        or ImageArtifact.objects.filter(node_id=image.node_id, reference=image.reference)
        .exclude(pk=image.pk)
        .exclude(state="deleted")
        .exists()
    )


@transaction.atomic
def keep_image(pk, actor):
    image = ImageArtifact.objects.select_for_update().get(pk=pk)
    if image.owner_id != actor.pk and (not actor.is_staff):
        raise ValidationError(tr("You need permission to manage this image."))
    if image.state != "ready":
        raise ValidationError(tr("Image deletion has started."))
    (image.temporary, image.recycle_after) = (False, None)
    image.save(update_fields=["temporary", "recycle_after"])
    audit(actor, "image_kept", image.pk, name=image.name)


@transaction.atomic
def request_image_deletion(pk, actor):
    image = ImageArtifact.objects.select_for_update().get(pk=pk)
    if image.owner_id != actor.pk and (not actor.is_staff):
        raise ValidationError(tr("You need permission to manage this image."))
    if image.shared or image.owner_id is None:
        raise ValidationError(tr("Shared base images are retained."))
    if image.state in {"deleting", "deleted"}:
        return image
    if image.state != "ready" or in_use(image):
        raise ValidationError(tr("The image is in use and cannot be deleted."))
    image.state = "deleting"
    image.verified = False
    image.recycle_after = None
    image.save(update_fields=["state", "verified", "recycle_after"])
    audit(actor, "image_delete_requested", image.pk, name=image.name)
    return image


@transaction.atomic
def prepare_collection(pk):
    image = ImageArtifact.objects.select_for_update().get(pk=pk)
    if image.shared or image.owner_id is None or image.state == "deleted":
        return None
    if in_use(image):
        if image.state == "ready" and image.recycle_after:
            image.recycle_after = None
            image.save(update_fields=["recycle_after"])
        return None
    if image.state == "deleting":
        return image
    if not image.recycle_after:
        if not image.temporary:
            return None
        settings = PlatformSettings.load()
        failed = ResourceRequest.objects.filter(image=image, status="failed").exists()
        hours = settings.failed_snapshot_hours if failed else settings.snapshot_hours
        image.recycle_after = timezone.now() + timedelta(hours=hours)
        image.save(update_fields=["recycle_after"])
        return None
    if image.recycle_after > timezone.now():
        return None
    (image.state, image.verified) = ("deleting", False)
    image.save(update_fields=["state", "verified"])
    return image


def collect_images(gateway, node):
    from gpu_platform_contracts.messages import Operation

    now = timezone.now()
    candidates = (
        ImageArtifact.objects.filter(node=node, shared=False)
        .exclude(state="deleted")
        .filter(Q(temporary=True) | Q(recycle_after__isnull=False) | Q(state="deleting"))
        .filter(Q(cleanup_retry_at__isnull=True) | Q(cleanup_retry_at__lte=now))
    )
    for pk in candidates.values_list("pk", flat=True):
        image = prepare_collection(pk)
        if image is None:
            continue
        try:
            gateway.request(
                node,
                Operation.IMAGE_DELETE,
                image_id=image.node_image_id or image.reference,
                owner=image.node_image_owner or str(image.owner_id),
            )
            with transaction.atomic():
                ImageArtifact.objects.filter(pk=pk, state="deleting").update(
                    state="deleted", deleted_at=timezone.now(), cleanup_error="", cleanup_retry_at=None
                )
                audit(
                    None,
                    "image_recycled",
                    pk,
                    name=image.name,
                    reference=image.reference,
                    size_bytes=image.size_bytes,
                )
        except Exception as exc:
            log.exception("Image collection failed: %s", pk)
            ImageArtifact.objects.filter(pk=pk).update(
                cleanup_error=type(exc).__name__, cleanup_retry_at=now + timedelta(minutes=5)
            )
