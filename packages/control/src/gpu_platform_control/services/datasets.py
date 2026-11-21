"""Dataset browsing and download authorization."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.translation import gettext_lazy as _
from gpu_platform_control.core.datasets import dataset_content, Download as Download
from gpu_platform_control.core.features import enabled, require
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.nodes.selection import selected_node
from .boundary import authenticated
from .read_models import materialize


@authenticated
def browse(actor, *, relative="", query="", node_id=None, management=False):
    if management and not actor.is_staff:
        raise PermissionDenied
    nodes = [node for node in ComputeNode.objects.filter(enabled=True) if enabled(node, "public_datasets")]
    if node_id is None or node_id == "":
        if not nodes:
            raise ValidationError(
                _("This feature is unavailable on the selected node."), code="feature_disabled"
            )
        node = nodes[0]
    else:
        node = selected_node(node_id)
        require(node, "public_datasets")
        if not node.enabled:
            raise ValidationError(_("The selected node has paused admission."), code="node_disabled")
    content = dataset_content(relative, query, node=node)
    return {"content": content, "node": materialize(node), "nodes": materialize(nodes)}
