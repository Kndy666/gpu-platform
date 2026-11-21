"""Save an environment and coordinate dependent program requests."""

from decimal import Decimal

from django.db import transaction
from gpu_platform_contracts.handoffs import Image
from gpu_platform_contracts.messages import Operation
from gpu_platform_control.slots.gateway import NodeCommandError
from gpu_platform_control.core.retention.models import EnvironmentSnapshot, ImageArtifact
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.accounts.models import UserProfile
from .services import audit, request_stop
from .tasks.lifecycle import mark_finished


def sync_snapshots(gateway, node):
    for snapshot in EnvironmentSnapshot.objects.filter(
        source__node=node, status__in=["pending", "working"]
    ).select_related("source__user", "target"):
        if (
            snapshot.target_id
            and Workspace.objects.filter(request_id=snapshot.target_id, stop_requested=True).exists()
        ):
            mark_finished(snapshot.target, "canceled")
            snapshot.status, snapshot.error = "failed", "task_canceled"
            snapshot.save(update_fields=["status", "error"])
            continue
        try:
            result = Image.model_validate(
                gateway.request(
                    node,
                    Operation.IMAGE_SAVE,
                    task_id=str(snapshot.source_id),
                    owner=str(snapshot.source.user_id),
                    image_id=str(snapshot.pk),
                )
            )
        except NodeCommandError as exc:
            if exc.message.code == "operation.pending":
                EnvironmentSnapshot.objects.filter(pk=snapshot.pk).update(status="working")
                continue
            snapshot.status, snapshot.error = "failed", exc.message.model_dump_json()
            snapshot.save(update_fields=["status", "error"])
            if snapshot.target_id:
                mark_finished(snapshot.target, "failed", error=snapshot.error)
            continue
        with transaction.atomic():
            UserProfile.objects.select_for_update().get(user=snapshot.source.user)
            from .quotas.accounting import check_image_quota
            from django.core.exceptions import ValidationError

            permitted = True
            try:
                check_image_quota(snapshot.source.user, node, Decimal(result.size_bytes) / Decimal(2**30))
            except ValidationError:
                permitted = False
            if not permitted:
                gateway.request(
                    node, Operation.IMAGE_DELETE, image_id=result.id, owner=str(snapshot.source.user_id)
                )
                snapshot.status, snapshot.error = "failed", "image_quota_exceeded"
                snapshot.save(update_fields=["status", "error"])
                if snapshot.target_id:
                    mark_finished(snapshot.target, "failed", error=snapshot.error)
                continue
            artifact, _ = ImageArtifact.objects.get_or_create(
                node=node,
                reference=result.reference,
                defaults={
                    "owner": snapshot.source.user,
                    "name": snapshot.source.purpose[:90],
                    "digest": result.digest,
                    "node_image_id": result.id,
                    "node_image_owner": result.owner,
                    "size_bytes": result.size_bytes,
                    "architecture": result.architecture,
                    "verified": True,
                    "temporary": bool(snapshot.target_id),
                },
            )
            snapshot.status, snapshot.artifact = "succeeded", artifact
            snapshot.save(update_fields=["status", "artifact"])
            if snapshot.target_id:
                changed = ResourceRequest.objects.filter(
                    pk=snapshot.target_id, status="preparing", workspace__stop_requested=False
                ).update(image=artifact, image_reference=result.reference, status="approved")
                if changed:
                    request_stop(snapshot.source_id, snapshot.source.user, "environment_saved")
            audit(snapshot.source.user, "environment_saved", snapshot.source_id, image=str(artifact.pk))
