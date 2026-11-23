from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from gpu_platform_contracts.messages import Operation
from gpu_platform_control.core.image_lifecycle import collect_images, keep_image, prepare_collection, request_image_deletion
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.tasks.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def saved_image():
    owner = get_user_model().objects.create_user("owner")
    node = ComputeNode.objects.create(name="node")
    image = ImageArtifact.objects.create(node=node, owner=owner, name="saved", verified=True, temporary=True,
        reference="registry.test/saved:1", node_image_id="saved", node_image_owner=str(owner.pk), size_bytes=1024)
    return owner, node, image


def test_image_retention_starts_after_confirmed_resource_release(saved_image):
    owner, _, image = saved_image
    request = ResourceRequest.objects.create(user=owner, image=image, node=image.node, purpose="task", status="succeeded", mode="batch")
    workspace = Workspace.objects.create(request=request)
    assert prepare_collection(image.pk) is None
    image.refresh_from_db()
    assert image.recycle_after is None
    workspace.cleanup_complete = True
    workspace.save()
    assert prepare_collection(image.pk) is None
    image.refresh_from_db()
    assert image.recycle_after > timezone.now()


def test_failed_image_delete_retries_without_admitting_new_tasks(saved_image):
    _, node, image = saved_image
    image.recycle_after = timezone.now() - timedelta(seconds=1)
    image.save()
    gateway = Mock()
    gateway.request.side_effect = ConnectionError()
    collect_images(gateway, node)
    image.refresh_from_db()
    assert image.state == "deleting"
    assert not image.verified
    assert image.cleanup_retry_at > timezone.now()
    ImageArtifact.objects.filter(pk=image.pk).update(cleanup_retry_at=timezone.now() - timedelta(seconds=1))
    gateway.request.side_effect = None
    collect_images(gateway, node)
    image.refresh_from_db()
    assert image.state == "deleted"
    assert image.deleted_at is not None
    gateway.request.assert_called_with(node, Operation.IMAGE_DELETE, image_id="saved", owner=str(image.owner_id))


def test_image_retention_actions_require_ownership(saved_image):
    owner, _, image = saved_image
    other = get_user_model().objects.create_user("other")
    for operation in (keep_image, request_image_deletion):
        with pytest.raises(ValidationError):
            operation(image.pk, other)
    keep_image(image.pk, owner)
    image.refresh_from_db()
    assert not image.temporary
    assert image.recycle_after is None
    request_image_deletion(image.pk, owner)
    image.refresh_from_db()
    assert image.state == "deleting"
    assert image.recycle_after is None
    assert not image.verified
