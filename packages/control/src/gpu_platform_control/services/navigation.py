"""Available user-facing operations from configured modules and node capabilities."""

from gpu_platform_control.core.features import enabled
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.slots.configuration import cluster_config
from .boundary import authenticated


@authenticated
def features(actor):
    nodes = list(ComputeNode.objects.filter(enabled=True))
    return {
        "public_datasets": any(enabled(node, "public_datasets") for node in nodes),
        "synchronization": any(
            enabled(node, "image_sync") or (actor.is_staff and enabled(node, "dataset_sync"))
            for node in nodes
        ),
        "notifications": bool(cluster_config().control.notify),
    }
