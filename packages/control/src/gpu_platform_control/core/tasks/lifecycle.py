from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from gpu_platform_control.core.retention.models import PlatformSettings, VolumeAllocation
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.accounts.models import UserProfile
from ..services import audit, work_volume, volume_in_use


@transaction.atomic
def reserve_dispatch(req, ws):
    from ..services import policy_for

    UserProfile.objects.select_for_update().get(user=req.user)
    policy = policy_for(req.user)
    ws.refresh_from_db()
    if ws.dispatch_started_at:
        return True
    if not policy.unlimited:
        active = [
            row.request
            for row in Workspace.objects.filter(
                request__user=req.user, cleanup_complete=False, dispatch_started_at__isnull=False
            )
            .exclude(pk=ws.pk)
            .select_related("request")
        ]
        if len(active) >= policy.concurrent_tasks:
            return False
    ws.dispatch_started_at = timezone.now()
    ws.save(update_fields=["dispatch_started_at"])
    return True


@transaction.atomic
def allocate_port(ws, occupied=()):
    ws.refresh_from_db()
    if ws.ssh_port:
        return
    from gpu_platform_control.slots.configuration import cluster_config

    settings = PlatformSettings.load()
    PlatformSettings.objects.select_for_update().get(pk=settings.pk)
    start, end = cluster_config().cluster.ssh_ports
    used = set(Workspace.objects.exclude(ssh_port=None).values_list("ssh_port", flat=True)) | set(occupied)
    for port in range(start, end + 1):
        if port not in used:
            ws.ssh_port = port
            ws.save(update_fields=["ssh_port"])
            return
    raise RuntimeError("login_port_pool_exhausted")


@transaction.atomic
def mark_finished(req, status, code=None, error="", ended_at=None):
    ws = Workspace.objects.get(request=req)
    if ws.ended_at:
        return True
    now = ended_at or timezone.now()
    if status == "expired":
        # Recheck under the write transaction: an extension may have committed
        # after this reconciler read the previous deadline.
        current_end = ResourceRequest.objects.only("ends_at").get(pk=req.pk).ends_at
        if current_end and current_end > now and current_end > req.ends_at:
            return False
    ws.ended_at, ws.exit_code, ws.error = now, code, error
    ws.save(update_fields=["ended_at", "exit_code", "error"])
    ResourceRequest.objects.filter(pk=req.pk).update(status=status)
    volume = work_volume(req)
    if volume is not None and not volume_in_use(volume, excluding=req.pk):
        VolumeAllocation.objects.filter(pk=volume.pk).update(
            retain_until=now + timedelta(days=PlatformSettings.load().workspace_retention_days)
        )
    if req.source_environment_id and volume is not None and volume.request_id == req.pk:
        original = work_volume(req.source_environment)
        if original.state not in {"deleting", "deleted"} and not volume_in_use(original, excluding=req.pk):
            VolumeAllocation.objects.filter(pk=original.pk).update(
                retain_until=now + timedelta(days=PlatformSettings.load().workspace_retention_days)
            )
    audit(None, "finished", req.pk, status=status, exit_code=code, error=error)
    return True
