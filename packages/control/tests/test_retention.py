from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from gpu_platform_contracts.messages import Operation
from gpu_platform_control.core.image_lifecycle import collect_images, prepare_collection
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact, PlatformSettings
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


@pytest.mark.parametrize(("status", "hours"), [("succeeded", 3), ("failed", 7)])
def test_image_retention_starts_after_confirmed_resource_release(saved_image, status, hours):
    owner, _, image = saved_image
    settings = PlatformSettings.load()
    settings.snapshot_hours = 3
    settings.failed_snapshot_hours = 7
    settings.save(update_fields=["snapshot_hours", "failed_snapshot_hours"])
    request = ResourceRequest.objects.create(
        user=owner, image=image, node=image.node, purpose="task", status=status, mode="batch"
    )
    workspace = Workspace.objects.create(request=request)
    assert prepare_collection(image.pk) is None
    image.refresh_from_db()
    assert image.recycle_after is None
    workspace.cleanup_complete = True
    workspace.save()
    released_at = timezone.now()
    assert prepare_collection(image.pk) is None
    image.refresh_from_db()
    assert released_at <= image.recycle_after - timedelta(hours=hours) <= timezone.now()


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


@pytest.mark.parametrize("as_staff", [False, True], ids=["owner", "staff"])
def test_image_deletion_authorizes_owners_and_staff_for_permanent_images(saved_image, as_staff):
    owner, node, temporary = saved_image
    other = get_user_model().objects.create_user("other")
    image = ImageArtifact.objects.create(
        node=node, owner=owner, name="private", reference="registry.test/private:1", verified=True
    )
    client = Client()
    url = f"/api/v1/images/{image.pk}"
    client.force_login(other)
    response = client.delete(url)
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    actor = get_user_model().objects.create_user("operator", is_staff=True) if as_staff else owner
    client.force_login(actor)
    assert client.delete(f"/api/v1/images/{temporary.pk}").status_code == 404
    temporary.refresh_from_db()
    assert temporary.state == "ready" and temporary.verified

    response = client.delete(url)
    assert response.status_code == 202
    data = response.json()["data"]
    assert (data["id"], data["state"], data["verified"]) == (str(image.pk), "deleting", False)
    assert response.json() == client.get(url).json()
    image.refresh_from_db()
    assert image.state == "deleting"
    assert image.recycle_after is None
    assert not image.verified
    assert client.delete(url).status_code == 202

    collect_images(Mock(), node)
    response = client.delete(url)
    assert response.status_code == 200
    assert response.json()["data"]["state"] == "deleted"
    assert response.json() == client.get(url).json()
