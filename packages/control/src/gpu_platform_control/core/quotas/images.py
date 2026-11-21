"""Owned image bytes plus durable reservations across all nodes."""

from django.db.models import Sum
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.sync.models import SyncJob


def allocated_bytes(user):
    stored = (
        ImageArtifact.objects.filter(owner=user)
        .exclude(state="deleted")
        .aggregate(total=Sum("size_bytes"))["total"]
        or 0
    )
    pending = (
        SyncJob.objects.filter(
            owner=user, kind="image", publish_shared=False, state__in=["queued", "running", "cleaning"]
        ).aggregate(total=Sum("reserved_bytes"))["total"]
        or 0
    )
    return stored + pending
