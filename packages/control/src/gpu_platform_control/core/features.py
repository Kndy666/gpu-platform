"""Capabilities used to decide which operations a node can accept."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as tr
from gpu_platform_control.slots.configuration import cluster_config


def provides(node, slot):
    modules = node.capabilities.get("modules")
    if modules is not None:
        return any(module["slot"] == slot for module in modules)
    host = cluster_config().hosts.get(node.name)
    return bool(host and host.slots.get(slot))


def enabled(node, feature):
    if feature in node.capabilities.get("features", {}):
        return node.capabilities["features"][feature]
    host = cluster_config().hosts.get(node.name)
    return bool(host and getattr(host.features, feature, False))


def require(node, feature):
    if not enabled(node, feature):
        raise ValidationError(
            tr("This server does not offer this feature. Choose a server that supports it."), code="feature_disabled"
        )
