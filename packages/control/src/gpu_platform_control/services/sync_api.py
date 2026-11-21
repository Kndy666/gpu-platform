from django.shortcuts import get_object_or_404
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact
from .accounts import set_language
from .operations import endpoint, body, success, page
from .synchronization import request_image_sync, request_dataset_sync, sync_jobs


def sync_data(job):
    return {
        "id": job.pk,
        "kind": job.kind,
        "source": job.source_id,
        "target": job.target_id,
        "state": job.state,
        "phase": job.phase,
        "image": job.image_id,
        "result_image": job.result_image_id,
        "paths": job.paths,
        "transferred_bytes": job.transferred_bytes,
        "total_bytes": job.total_bytes,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "ended_at": job.ended_at,
    }


@endpoint("GET")
def jobs(request):
    return page(request, sync_jobs(request.user), sync_data)


@endpoint("POST", idempotent=True)
def image_sync(request, pk):
    data = body(request, {"target", "bandwidth"})
    image = get_object_or_404(ImageArtifact, pk=pk)
    target = get_object_or_404(ComputeNode, pk=data.get("target"))
    job = request_image_sync(
        request.user, image, target, bandwidth=data.get("bandwidth", 0),
    )
    return success(sync_data(job), 202)


@endpoint("POST", staff=True, idempotent=True)
def dataset_sync(request):
    data = body(request, {"source", "target", "paths", "bandwidth"})
    source = get_object_or_404(ComputeNode, pk=data.get("source"))
    target = get_object_or_404(ComputeNode, pk=data.get("target"))
    job = request_dataset_sync(
        request.user, source, target, data.get("paths", []), bandwidth=data.get("bandwidth", 0)
    )
    return success(sync_data(job), 202)


@endpoint("PATCH")
def language(request):
    return success(set_language(request.user, body(request, {"language"}).get("language", "")))


@endpoint("GET", "PATCH")
def notifications(request):
    from .notifications import notification_channels, set_notification_receiver

    if request.method == "PATCH":
        data = body(request, {"channel", "values"})
        return success(set_notification_receiver(request.user, data.get("channel"), data.get("values", {})))
    return success({"channels": notification_channels(request.user)})
