"""Authorization, accounting, and durable progress for node-to-node transfers."""

from pathlib import PurePosixPath
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.sync.models import SyncJob
from gpu_platform_control.core.accounts.models import UserProfile
from gpu_platform_control.core.services import audit
from .boundary import authenticated
from .errors import message

GIB = 1024**3


class UncertainImport(ConnectionError):
    """A node may have committed an import whose reply was lost."""


def failure(code):
    raise ValidationError(message(code), code=code)


def _enabled(node, feature):
    if not node.enabled or node.state != "online":
        failure("node_unavailable")
    if not node.capabilities.get("features", {}).get(feature, False):
        failure("feature_disabled")


@authenticated
@transaction.atomic
def request_image_sync(user, image, target, *, bandwidth=0):
    if not user.is_staff and (image.owner_id != user.pk or image.shared):
        raise PermissionDenied
    publish_shared = user.is_staff and image.shared
    owner = image.owner if image.owner_id and not image.shared else user
    UserProfile.objects.select_for_update().get(user=owner)
    if image.state != "ready" or not image.verified:
        failure("image_unavailable")
    if image.node_id == target.pk:
        failure("sync_target_is_source")
    if image.architecture != target.architecture:
        failure("image_architecture_mismatch")
    _enabled(image.node, "image_sync")
    _enabled(target, "image_sync")
    if bandwidth < 0:
        failure("bandwidth_invalid")
    existing = SyncJob.objects.filter(
        owner=owner, image=image, target=target, state__in=["queued", "running", "cleaning"]
    ).first()
    if existing:
        if existing.publish_shared != publish_shared:
            failure("sync_conflicting_request")
        return existing
    from decimal import Decimal
    from gpu_platform_control.core.quotas.accounting import check_image_quota

    if not publish_shared:
        check_image_quota(owner, target, Decimal(image.size_bytes) / Decimal(GIB))
    job = SyncJob.objects.create(
        owner=owner,
        source=image.node,
        target=target,
        image=image,
        kind="image",
        publish_shared=publish_shared,
        total_bytes=image.size_bytes,
        reserved_bytes=image.size_bytes,
        bandwidth_bytes_per_second=bandwidth,
    )
    audit(
        user,
        "sync_requested",
        str(job.pk),
        kind="image",
        source_node=image.node.name,
        target_node=target.name,
    )
    return job


@authenticated
@transaction.atomic
def request_dataset_sync(user, source, target, paths=(), *, bandwidth=0):
    if not user.is_staff:
        raise PermissionDenied
    if source.pk == target.pk:
        failure("sync_target_is_source")
    _enabled(source, "dataset_sync")
    _enabled(target, "dataset_sync")
    normalized = []
    for path in paths:
        value = PurePosixPath(path)
        if value.is_absolute() or ".." in value.parts or "\x00" in path:
            failure("dataset_path_invalid")
        normalized.append(str(value))
    if bandwidth < 0:
        failure("bandwidth_invalid")
    job = SyncJob.objects.create(
        owner=user,
        source=source,
        target=target,
        kind="datasets",
        paths=normalized,
        bandwidth_bytes_per_second=bandwidth,
    )
    audit(
        user, "sync_requested", str(job.pk), kind="datasets", source_node=source.name, target_node=target.name
    )
    return job


@authenticated
def sync_images(user):
    query = ImageArtifact.objects.filter(state="ready", verified=True)
    if not user.is_staff:
        query = query.filter(owner=user, shared=False)
    return query.select_related("node", "owner")


@authenticated
def sync_jobs(user):
    query = SyncJob.objects.all()
    if not user.is_staff:
        query = query.filter(owner=user, kind="image")
    return query.select_related("source", "target").order_by("-created_at")


def run_sync(job_id, transfer, cleanup=None):
    """Transfer receives a frozen job and a progress callback, then returns image metadata."""
    with transaction.atomic():
        job = (
            SyncJob.objects.select_for_update()
            .select_related("image", "source", "target", "owner")
            .get(pk=job_id)
        )
        if job.state != "queued":
            return job
        job.state = "running"
        job.started_at = timezone.now()
        job.save(update_fields=["state", "started_at"])

    def progress(transferred, total=None):
        if transferred < 0 or (total is not None and (total < transferred or total < 0)):
            failure("sync_progress_invalid")
        fields = {"transferred_bytes": transferred}
        if total is not None:
            fields["total_bytes"] = total
        SyncJob.objects.filter(pk=job.pk, state="running", transferred_bytes__lte=transferred).update(
            **fields
        )

    result = None
    try:
        result = transfer(job, progress)
        with transaction.atomic():
            job = SyncJob.objects.select_for_update().get(pk=job_id)
            if job.kind == "image":
                source = job.image
                size = int(result["size_bytes"])
                if size < 0 or size > job.reserved_bytes:
                    failure("sync_size_exceeded")
                job.result_image, _ = ImageArtifact.objects.get_or_create(
                    node=job.target,
                    node_image_id=result.get("id", result["reference"]),
                    defaults={
                        "owner": None if job.publish_shared else job.owner,
                        "name": source.name,
                        "reference": result["reference"],
                        "node_image_owner": result.get(
                            "owner", "shared" if job.publish_shared else str(job.owner_id)
                        ),
                        "digest": result.get("digest", source.digest),
                        "size_bytes": size,
                        "architecture": source.architecture,
                        "verified": True,
                        "shared": job.publish_shared,
                    },
                )
            job.state = "completed"
            job.ended_at = timezone.now()
            job.transferred_bytes = job.total_bytes
            job.save()
            audit(job.owner, "sync_completed", str(job.pk), kind=job.kind)
    except Exception as exc:
        code = exc.code if isinstance(exc, ValidationError) and hasattr(exc, "code") else "sync_failed"
        if isinstance(exc, UncertainImport):
            SyncJob.objects.filter(pk=job_id).update(
                state="queued", error={"code": "sync_delivery_uncertain", "parameters": {}}
            )
        elif result and job.kind == "image":
            cleanup_image = {
                "id": result.get("id", result.get("reference", "")),
                "owner": "shared" if job.publish_shared else str(job.owner_id),
            }
            SyncJob.objects.filter(pk=job_id).update(
                state="cleaning", cleanup_image=cleanup_image, error={"code": code, "parameters": {}}
            )
            job.refresh_from_db()
            if cleanup:
                finish_cleanup(job, cleanup)
        else:
            SyncJob.objects.filter(pk=job_id).update(
                state="failed", error={"code": code, "parameters": {}}, ended_at=timezone.now()
            )
        job.refresh_from_db()
    return job


def finish_cleanup(job, cleanup):
    try:
        cleanup(job, job.cleanup_image)
    except Exception:
        return False
    SyncJob.objects.filter(pk=job.pk, state="cleaning").update(
        state="failed", cleanup_image={}, ended_at=timezone.now()
    )
    return True
