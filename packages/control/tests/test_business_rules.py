from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from gpu_platform_control.core.quotas.models import RegisteredResource, NodeResource, ResourceQuota
from gpu_platform_control.core.approvals import request_extension, review_extension
from gpu_platform_control.core.nodes.models import ComputeNode, StoragePool
from gpu_platform_control.core.retention.models import ImageArtifact, VolumeAllocation
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.accounts.models import SSHKey
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.services import approve, request_stop, validate_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def application():
    users = get_user_model()
    actor = users.objects.create_user("reviewer", is_staff=True)
    owner = users.objects.create_user("researcher")
    other = users.objects.create_user("observer")
    owner.profile.extension_limit = 2
    owner.profile.approval_mode = "manual"
    owner.profile.save(update_fields=["extension_limit", "approval_mode"])
    node = ComputeNode.objects.create(name="execution", state="online", last_seen=timezone.now(), architecture="amd64")
    for key, values in {
        "cpu": {"unit": "core", "minimum": 1},
        "memory": {"unit": "GiB", "minimum": 1},
        "home_storage": {"unit": "GiB", "accounting": "volume", "volume_kind": "home", "scope": "node"},
        "work_storage": {"unit": "GiB", "accounting": "volume", "volume_kind": "work"},
    }.items():
        resource = RegisteredResource.objects.create(key=key, label={"en": key}, **values)
        NodeResource.objects.create(node=node, resource=resource, total=1000)
        ResourceQuota.objects.create(group=owner.profile.group, resource=resource, limit=1000)
    pool = StoragePool.objects.create(node=node, name="data", capacity_gib=1000, free_gib=1000,
                                      healthy=True, observed_at=timezone.now())
    image = ImageArtifact.objects.create(node=node, name="environment", owner=owner, verified=True,
        reference="registry.test/environment", digest="sha256:" + "a" * 64, architecture="amd64")
    SSHKey.objects.create(user=owner, name="login", public_key="ssh-ed25519 fixture", fingerprint="fixture")
    request = ResourceRequest.objects.create(user=owner, node=node, image=image, mode="interactive",
        purpose="analysis", resource_quantities={"cpu": 2, "memory": 4},
        volume_sizes={"home": owner.profile.home_size_gib, "work": 20},
        starts_at=timezone.now() - timedelta(minutes=1), ends_at=timezone.now() + timedelta(hours=1))
    return actor, owner, other, request, pool


def test_approval_is_atomic_and_idempotent(application):
    actor, owner, _, request, pool = application
    approve(request.pk, actor)
    approve(request.pk, actor)
    assert Workspace.objects.filter(request=request).count() == 1
    assert VolumeAllocation.objects.filter(user=owner).count() == 2
    pool.refresh_from_db()
    assert pool.reserved_gib == owner.profile.home_size_gib + request.volume_sizes["work"]


def test_capacity_failure_rolls_back_all_reservations(application):
    actor, owner, _, request, pool = application
    pool.free_gib = owner.profile.home_size_gib + 1
    pool.buffer_percent = 0
    pool.save()
    with pytest.raises(ValidationError):
        approve(request.pk, actor)
    request.refresh_from_db()
    pool.refresh_from_db()
    assert request.status == "submitted"
    assert pool.reserved_gib == 0
    assert not VolumeAllocation.objects.exists()
    assert not Workspace.objects.exists()


def test_owner_and_reviewer_permissions_apply_before_mutation(application):
    actor, owner, other, request, _ = application
    with pytest.raises(ValidationError):
        approve(request.pk, other)
    with pytest.raises(ValidationError):
        request_stop(request.pk, other)
    request.refresh_from_db()
    assert request.status == "submitted"
    approve(request.pk, actor)
    request_stop(request.pk, owner)
    assert Workspace.objects.get(request=request).stop_requested


def test_extension_commits_only_after_node_confirmation(application):
    actor, owner, _, request, _ = application
    approve(request.pk, actor)
    Workspace.objects.filter(request=request).update(dispatch_started_at=timezone.now(), expire_at=request.ends_at)
    extension = request_extension(request.pk, owner, request.ends_at + timedelta(hours=1), "More runtime")
    with patch("gpu_platform_control.slots.gateway.Gateway") as gateway:
        gateway.return_value.request.side_effect = ConnectionError()
        with pytest.raises(ValidationError):
            review_extension(extension.pk, actor, "approve")
        gateway.return_value.request.side_effect = None
        review_extension(extension.pk, actor, "approve")
    request.refresh_from_db()
    extension.refresh_from_db()
    assert request.ends_at == extension.ends_at
    assert request.extension_count == 1
    assert extension.status == "approved"
    assert Workspace.objects.get(request=request).expire_at == extension.ends_at


def test_rejected_extension_does_not_consume_allowance(application):
    actor, owner, other, request, _ = application
    approve(request.pk, actor)
    with pytest.raises(ValidationError):
        request_extension(request.pk, other, request.ends_at + timedelta(hours=1), "More runtime")
    extension = request_extension(request.pk, owner, request.ends_at + timedelta(hours=1), "More runtime")
    with pytest.raises(ValidationError):
        request_extension(request.pk, owner, request.ends_at + timedelta(hours=2), "Duplicate")
    review_extension(extension.pk, actor, "reject", "Resources reserved")
    request.refresh_from_db()
    assert request.extension_count == 0
    assert request_extension(request.pk, owner, request.ends_at + timedelta(hours=1), "Retry").status == "pending"


@pytest.mark.parametrize("change", [{"working_dir": "/home/observer"}, {"environment": {"PATH": "/tmp"}},
                                      {"environment": {"CUDA_VISIBLE_DEVICES": "0"}}])
def test_batch_input_cannot_change_environment_identity(application, change):
    _, _, _, request, _ = application
    request.mode = "batch"
    request.argv = ["python", "train.py"]
    for name, value in change.items():
        setattr(request, name, value)
    with pytest.raises(ValidationError):
        validate_request(request)


@pytest.mark.parametrize("scope, allowed", [("node", True), ("cluster", False)])
def test_image_accounting_follows_the_registered_scope(application, scope, allowed):
    from gpu_platform_control.core.quotas.accounting import check_image_quota
    _, owner, _, request, _ = application
    resource = RegisteredResource.objects.create(key="images", unit="GiB", label={"en": "Images"},
        accounting="image", scope=scope)
    NodeResource.objects.create(node=request.node, resource=resource, total=1000)
    owner.profile.resource_limits.setdefault("resources", {})["images"] = 60
    owner.profile.save(update_fields=["resource_limits"])
    other = ComputeNode.objects.create(name="other")
    ImageArtifact.objects.create(node=other, owner=owner, reference="fixture", size_bytes=50*2**30)
    if allowed:
        check_image_quota(owner, request.node, 20)
    else:
        with pytest.raises(ValidationError):
            check_image_quota(owner, request.node, 20)


def test_work_volume_limit_applies_to_each_container(application):
    actor, owner, _, request, pool = application
    owner.profile.resource_limits.setdefault("resources", {})["work_storage"] = 20
    owner.profile.save(update_fields=["resource_limits"])
    VolumeAllocation.objects.create(node=request.node, user=owner, kind="work", pool=pool, size_gib=20)
    approve(request.pk, actor)
    assert Workspace.objects.filter(request=request).exists()
    assert list(VolumeAllocation.objects.filter(user=owner, kind="work").values_list("size_gib", flat=True)) == [20, 20]


def test_home_capacity_is_set_once_on_the_member(application):
    from gpu_platform_control.core.services import resize_home
    actor, owner, _, _, _ = application
    ResourceQuota.objects.filter(resource_id="home_storage").delete()
    resize_home(owner, 150, actor)
    owner.profile.refresh_from_db()
    assert owner.profile.home_size_gib == 150
    assert not VolumeAllocation.objects.filter(user=owner).exists()
    with pytest.raises(ValidationError):
        resize_home(owner, 0, actor)


def test_home_expansion_checks_each_physical_pool_atomically(application):
    from gpu_platform_control.core.services import resize_home
    actor, owner, _, request, pool = application
    ResourceQuota.objects.filter(resource_id="home_storage").delete()
    other = ComputeNode.objects.create(name="other")
    second = StoragePool.objects.create(node=other, name="data", capacity_gib=1000, free_gib=1000,
        healthy=True, observed_at=timezone.now())
    for node, storage in [(request.node, pool), (other, second)]:
        VolumeAllocation.objects.create(node=node, user=owner, kind="home", pool=storage, size_gib=50)
    resize_home(owner, 150, actor)
    assert list(VolumeAllocation.objects.filter(user=owner).values_list("size_gib", flat=True)) == [150, 150]
    second.free_gib = 150
    second.save(update_fields=["free_gib"])
    with pytest.raises(ValidationError):
        resize_home(owner, 300, actor)
    assert list(VolumeAllocation.objects.filter(user=owner).values_list("size_gib", flat=True)) == [150, 150]
    owner.profile.refresh_from_db()
    assert owner.profile.home_size_gib == 150
