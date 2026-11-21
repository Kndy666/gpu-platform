from django.utils import timezone
from gpu_platform_contracts.handoffs import VolumeRequest
from gpu_platform_contracts.messages import Operation
from gpu_platform_control.core.retention.models import VolumeAllocation
from gpu_platform_control.core.volumes import collect_volume_deletions, request_volume_deletion, volume_busy
from gpu_platform_control.slots.gateway import NodeCommandError


def sync_storage(gateway, node):
    for volume in VolumeAllocation.objects.filter(node=node, state__in=["reserved", "bound"]).select_related(
        "user"
    ):
        try:
            if volume.handoff:
                if str(volume.handoff["size_gib"]) == str(volume.size_gib):
                    continue
                result = gateway.request(
                    node,
                    Operation.STORAGE_RESIZE,
                    volume_id=str(volume.pk),
                    owner=str(volume.user_id),
                    size_gib=volume.size_gib,
                )
            else:
                mount_path = "/home/" + volume.user.username if volume.kind == "home" else "/workspace"
                request = VolumeRequest(
                    id=str(volume.pk), size_gib=volume.size_gib, kind=volume.kind, mount_path=mount_path
                )
                result = gateway.request(
                    node,
                    Operation.STORAGE_PREPARE,
                    owner=str(volume.user_id),
                    request=request.model_dump(mode="json"),
                )
            volume.handoff, volume.location, volume.state = result, result["location"], "bound"
            volume.save(update_fields=["handoff", "location", "state"])
        except NodeCommandError as exc:
            if exc.message.code != "operation.pending":
                VolumeAllocation.objects.filter(pk=volume.pk).update(
                    deletion_error=exc.message.model_dump_json()
                )
    for volume in VolumeAllocation.objects.filter(
        node=node, kind="work", state="bound", retain_until__lte=timezone.now()
    ):
        if not volume_busy(volume):
            request_volume_deletion(volume.pk, None)
    collect_volume_deletions(gateway, node)
