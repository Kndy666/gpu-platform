"""Turn configured transfer schedules into durable jobs."""

import hashlib
import json
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.sync.models import SyncJob
from gpu_platform_control.slots.configuration import cluster_config
from .synchronization import request_dataset_sync, request_image_sync


def identity(kind, policy, target):
    payload = json.dumps([kind, policy.model_dump(mode="json"), target], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@transaction.atomic
def schedule_transfers(now=None):
    now = now or timezone.now()
    admin = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
    if admin is None:
        return []
    from gpu_platform_control.core.accounts.models import UserProfile

    UserProfile.objects.select_for_update().get(user=admin)
    config = cluster_config().control.sync
    nodes = {node.name: node for node in ComputeNode.objects.filter(enabled=True)}
    jobs = []
    for kind, policies in [("image", config.images), ("datasets", config.datasets)]:
        for policy in policies:
            source = nodes.get(policy.source)
            if source is None:
                continue
            for target_name in policy.targets:
                target = nodes.get(target_name)
                if target is None:
                    continue
                key = identity(kind, policy, target_name)
                previous = SyncJob.objects.filter(policy_key=key).order_by("-created_at").first()
                if previous and previous.state in {"queued", "running", "cleaning"}:
                    continue
                interval = policy.interval_seconds if kind == "datasets" else 3600
                if previous and previous.ended_at and previous.ended_at + timedelta(seconds=interval) > now:
                    continue
                try:
                    if kind == "image":
                        image = ImageArtifact.objects.filter(
                            node=source, reference=policy.image, shared=True, verified=True, state="ready"
                        ).first()
                        if image is None:
                            continue
                        if previous and previous.state == "completed" and previous.result_image_id:
                            copied = previous.result_image
                            if copied.state == "ready" and copied.digest == image.digest:
                                continue
                        job = request_image_sync(
                            admin,
                            image,
                            target,
                            bandwidth=policy.bandwidth_bytes_per_second,
                        )
                    else:
                        job = request_dataset_sync(
                            admin, source, target, policy.paths, bandwidth=policy.bandwidth_bytes_per_second
                        )
                except ValidationError:
                    continue
                job.policy_key = key
                job.save(update_fields=["policy_key"])
                jobs.append(job)
    return jobs
