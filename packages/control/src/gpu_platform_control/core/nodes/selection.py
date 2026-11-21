"""Configured node identities and admission controls."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as tr
from gpu_platform_control.core.nodes.models import ComputeNode, default_node_id


def selected_node(value=None):
    try:
        identifier = int(value) if value is not None and value != "" else default_node_id()
    except (ValueError, TypeError) as exc:
        raise ValidationError(tr("Invalid node"), code="node_invalid") from exc
    try:
        return ComputeNode.objects.get(pk=identifier)
    except ComputeNode.DoesNotExist as exc:
        raise ValidationError(tr("The selected node is unavailable."), code="node_not_found") from exc
