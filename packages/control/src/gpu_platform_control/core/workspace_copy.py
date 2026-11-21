"""Coordinate a stopped work-volume copy through the storage contract."""

from django.utils import timezone
from gpu_platform_contracts.handoffs import VolumeRequest
from gpu_platform_contracts.messages import Operation
from gpu_platform_control.slots.gateway import NodeCommandError
from gpu_platform_control.core.retention.models import VolumeAllocation
from .services import audit, work_volume


def ensure_workspace_copy(gateway, request):
    destination = getattr(request, "volumeallocation", None)
    if (
        not request.source_environment_id
        or not destination
        or destination.state not in {"copy_pending", "copying"}
    ):
        return True
    if not request.source_environment.workspace.cleanup_complete:
        return False
    source = work_volume(request.source_environment)
    if source.state in {"deleting", "deleted"}:
        raise ValueError("source_volume_unavailable")
    spec = VolumeRequest(
        id=str(destination.pk), size_gib=destination.size_gib, kind="work", mount_path="/workspace"
    )
    try:
        result = gateway.request(
            request.node,
            Operation.STORAGE_COPY,
            volume_id=str(source.pk),
            owner=str(source.user_id),
            destination=spec.model_dump(mode="json"),
        )
    except NodeCommandError as exc:
        if exc.message.code == "operation.pending":
            VolumeAllocation.objects.filter(pk=destination.pk).update(state="copying")
            return False
        raise
    destination.state, destination.handoff, destination.location = "bound", result, result["location"]
    destination.save(update_fields=["state", "handoff", "location"])
    VolumeAllocation.objects.filter(pk=source.pk).update(retain_until=timezone.now())
    audit(request.user, "workspace_copied", request.pk, size_gib=destination.size_gib)
    return True
