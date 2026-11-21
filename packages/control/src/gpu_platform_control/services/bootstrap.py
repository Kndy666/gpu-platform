"""Create the configured default image build once for each eligible node."""

from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact, ImageBuild
from gpu_platform_control.slots.configuration import cluster_config


@transaction.atomic
def ensure_default_image(node, *, recipe=None, context=None, rebuild=False):
    node = ComputeNode.objects.select_for_update().get(pk=node.pk)
    if (
        not rebuild
        and ImageArtifact.objects.filter(node=node, shared=True, verified=True, state="ready").exists()
    ):
        return None
    existing = ImageBuild.objects.filter(node=node, bootstrap=True).order_by("-created_at").first()
    if existing and (existing.status in {"submitted", "building"} or not rebuild):
        return existing
    host = cluster_config().hosts.get(node.name)
    if host is None:
        return None
    if not host.slots.get("build") or not host.slots.get("registry"):
        return None
    recipe = recipe or host.parameters.get("bootstrap_recipe")
    context = context or host.parameters.get("bootstrap_context", "")
    if not recipe:
        return None
    path = Path(recipe)
    if not path.is_absolute():
        path = settings.DATA_DIR / path
    if not path.is_file():
        return None
    if not str(context).startswith("trusted:"):
        raise ValidationError("bootstrap_context_invalid", code="bootstrap_context_invalid")
    administrator = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
    if administrator is None:
        return None
    return ImageBuild.objects.create(
        node=node,
        user=administrator,
        name="Standard environment",
        dockerfile=path.read_text(),
        context_ref=context,
        bootstrap=True,
    )
