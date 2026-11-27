import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from gpu_platform_contracts.handoffs import Image
from gpu_platform_contracts.messages import CapabilityReport
from gpu_platform_control.core.builds import BuildReconciler
from gpu_platform_control.core.features import require
from gpu_platform_control.services.catalog import build_options
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact, ImageBuild
from gpu_platform_control.services.resources import register_capabilities

pytestmark = pytest.mark.django_db


def test_runtime_catalog_allows_default_image_without_registry_or_builder():
    node = ComputeNode.objects.create(name='main')
    report = CapabilityReport(node_id='main', architecture='amd64', images=[Image(
        id='base', owner='shared', node_id='main', architecture='amd64', form='reference',
        reference='local/base:latest', digest='sha256:abc', size_bytes=100)],
        features={'save_environment': False, 'image_sync': False})
    register_capabilities(node.pk, report)
    artifact = ImageArtifact.objects.get(node=node)
    assert artifact.node_image_id == 'base'
    assert artifact.shared and artifact.verified
    assert artifact.owner_id is None
    user = get_user_model().objects.create_user('reader')
    assert not build_options(user)['nodes']
    node.refresh_from_db()
    with pytest.raises(ValidationError) as error:
        require(node, 'save_environment')
    assert error.value.code == 'feature_disabled'


def test_build_image_inputs_enforce_ownership_and_omit_credentials():
    user = get_user_model().objects.create_user('owner')
    other = get_user_model().objects.create_user('other')
    node = ComputeNode.objects.create(name='main')
    mine = ImageArtifact.objects.create(node=node, owner=user, name='mine', node_image_id='mine', node_image_owner=str(user.pk), reference='registry/mine', verified=True)
    shared = ImageArtifact.objects.create(node=node, shared=True, name='shared', node_image_id='base', node_image_owner='shared', reference='registry/base', verified=True)
    ImageArtifact.objects.create(node=node, owner=other, name='other', reference='registry/other', verified=True)
    ImageArtifact.objects.create(
        node=node, owner=user, name='temporary', reference='registry/temporary', verified=True, temporary=True
    )
    build = ImageBuild.objects.create(node=node, user=user, name='new', dockerfile='FROM registry/mine')
    images = BuildReconciler(None, node).authorized_images(build)
    assert {image.reference for image in images} == {mine.reference, shared.reference}
    assert all(not image.credentials for image in images)


def test_pending_build_publication_remains_active_for_later_reconciliation():
    from unittest.mock import Mock
    from gpu_platform_contracts.texts import Message
    from gpu_platform_control.slots.gateway import NodeCommandError
    user = get_user_model().objects.create_user('owner')
    node = ComputeNode.objects.create(name='main')
    build = ImageBuild.objects.create(node=node, user=user, name='new', dockerfile='FROM scratch')
    gateway = Mock()
    gateway.request.side_effect = NodeCommandError(Message(code='operation.pending'))
    BuildReconciler(gateway, node).tick()
    build.refresh_from_db()
    assert build.status == 'building'
    assert build.artifact_id is None
    assert not ImageArtifact.objects.exists()
