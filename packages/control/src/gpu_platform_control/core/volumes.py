from django.utils.translation import gettext_lazy as tr
import logging
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from gpu_platform_control.core.retention.models import EnvironmentSnapshot, VolumeAllocation
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.nodes.models import StoragePool
from .services import ACTIVE, audit

log = logging.getLogger(__name__)


def related_requests(volume):
    requests = ResourceRequest.objects.filter(node_id=volume.node_id, user_id=volume.user_id)
    if volume.kind == "work":
        requests = requests.filter(
            Q(pk=volume.request_id)
            | Q(source_environment_id=volume.request_id)
            & (Q(volumeallocation__isnull=True) | Q(volumeallocation__state__in=["copy_pending", "copying"]))
        )
    return requests


def volume_busy(volume):
    requests = related_requests(volume)
    return (
        requests.filter(Q(status__in=ACTIVE | {"submitted"}) | Q(workspace__cleanup_complete=False)).exists()
        or EnvironmentSnapshot.objects.filter(source__in=requests, status__in=["pending", "working"]).exists()
    )


@transaction.atomic
def request_volume_deletion(pk, actor):
    if actor is not None and (not actor.is_staff):
        raise ValidationError(tr("Administrator permission is required."))
    volume = VolumeAllocation.objects.get(pk=pk)
    if volume.state in {"deleted", "deleting"}:
        return
    if actor is None and volume.kind != "work":
        raise ValidationError(tr("Home volume deletion requires an administrator."))
    if volume_busy(volume):
        raise ValidationError(tr("The volume is in use. Stop its tasks and wait for resource release."))
    (volume.state, volume.deletion_error) = ("deleting", "")
    volume.save(update_fields=["state", "deletion_error"])
    audit(
        actor,
        "volume_delete_requested",
        volume.pk,
        location=volume.location,
        kind=volume.kind,
        username=volume.user.username,
        size_gib=volume.size_gib,
    )


def collect_volume_deletions(gateway, node):
    from gpu_platform_contracts.messages import Operation

    for volume in VolumeAllocation.objects.filter(node=node, state="deleting"):
        if volume_busy(volume):
            continue
        try:
            gateway.request(
                node, Operation.STORAGE_DELETE, volume_id=str(volume.pk), owner=str(volume.user_id)
            )
            with transaction.atomic():
                volume.refresh_from_db()
                if volume.state != "deleting" or volume_busy(volume):
                    continue
                (volume.state, volume.deletion_error) = ("deleted", "")
                volume.save(update_fields=["state", "deletion_error"])
                StoragePool.objects.filter(pk=volume.pool_id).update(
                    reserved_gib=F("reserved_gib") - volume.size_gib
                )
                if volume.kind == "work":
                    ResourceRequest.objects.filter(pk=volume.request_id).update(status="cleaned")
                audit(None, "volume_deleted", volume.pk, kind=volume.kind, size_gib=volume.size_gib)
        except Exception as exc:
            VolumeAllocation.objects.filter(pk=volume.pk, state="deleting").update(
                deletion_error=type(exc).__name__
            )


def annotate_volume_usage(volumes):
    owners = {volume.user_id for volume in volumes}
    requests = ResourceRequest.objects.filter(user_id__in=owners).filter(
        Q(status__in=ACTIVE | {"submitted"}) | Q(workspace__cleanup_complete=False)
    )
    (busy_users, busy_requests) = (set(), set())
    for user_id, node_id, pk, source in requests.values_list(
        "user_id", "node_id", "pk", "source_environment_id"
    ):
        busy_users.add((user_id, node_id))
        busy_requests.update({pk, source})
    for user_id, node_id, source in EnvironmentSnapshot.objects.filter(
        source__user_id__in=owners, status__in=["pending", "working"]
    ).values_list("source__user_id", "source__node_id", "source_id"):
        busy_users.add((user_id, node_id))
        busy_requests.add(source)
    for volume in volumes:
        volume.busy = (
            (volume.user_id, volume.node_id) in busy_users
            if volume.kind == "home"
            else volume.request_id in busy_requests
        )
