from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from gpu_platform_contracts.messages import CapabilityReport
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.resources import ResourceDefinition, ResourceQuotaRule, ResourceUsage
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.policies import effective_policy
from gpu_platform_control.core.quotas.models import ResourceQuota, ResourceReservation
from gpu_platform_control.core.quotas.rules import quota_rules, resource_limit
from gpu_platform_control.services.catalog import group_options, member_options, request_options
from gpu_platform_control.services.commands import save_group, save_member, submit_task
from gpu_platform_control.services.resources import register_capabilities, reserve_resources, release_resources

pytestmark = pytest.mark.django_db


@pytest.fixture
def converted_quota():
    from gpu_platform_control.core.accounts.models import SSHKey
    from gpu_platform_control.core.retention.models import ImageArtifact

    user = get_user_model().objects.create_user("quota-owner", is_staff=True, is_superuser=True)
    user.profile.approval_mode = "manual"
    user.profile.save(update_fields=["approval_mode"])
    SSHKey.objects.create(user=user, name="fixture", public_key="ssh-ed25519 fixture", fingerprint="fixture")
    node = ComputeNode.objects.create(name="main", state="online")
    source = ResourceDefinition(key="capacity", unit="percent", label=LocalizedText(en="Capacity"),
                                allocation_group="device", per_allocation=True)
    descriptor = ModuleDescriptor(name="fixture", slot="gpu", side="node", version="1",
        resources=[ResourceDefinition(key="devices", unit="count", integral=True, minimum=0,
                                      label=LocalizedText(en="Devices"))],
        quota_rules={"devices": ResourceQuotaRule(source=source, basis="per_allocation", divisor=100)})
    report = CapabilityReport(node_id=node.name, architecture="amd64", modules=[descriptor],
                              resources=ResourceUsage(total={"devices": 8}))
    register_capabilities(node.pk, report)
    node.refresh_from_db()
    ResourceQuota.objects.create(group=user.profile.group, resource_id="capacity", limit=400,
                                  per_allocation_limit=150)
    image = ImageArtifact.objects.create(node=node, shared=True, name="fixture", reference="fixture:latest",
                                          verified=True, architecture="amd64")
    values = {"node": node.pk, "image": image.pk, "mode": "batch", "argv": ["true"],
              "purpose": "quota fixture",
              "resource_quantities": {"devices": 1}, "volume_sizes": {"home": 50, "work": 20}}
    return user, node, report, values


@pytest.mark.parametrize(("percent", "expected"), [(0, 0), (99, 0), (100, 1), (150, 1), (199.999, 1), (200, 2)])
def test_converted_quota_floors_per_device_percentage(converted_quota, percent, expected):
    user, node, _, _ = converted_quota
    user.profile.resource_limits = {"per_allocation": {"capacity": str(percent)}}
    user.profile.save(update_fields=["resource_limits"])
    policy = effective_policy(user)
    assert policy.resources["capacity"] == 400
    assert resource_limit(policy, "devices", rules=quota_rules(node)) == expected
    assert request_options(user, node.pk)["resources"][0]["limit"] == expected


def test_policy_options_expose_source_without_independent_converted_quota(converted_quota):
    user, _, _, _ = converted_quota
    for options in (group_options(user, user.profile.group_id), member_options(user, user.pk)):
        resources = {row["key"]: row for row in options["resources"]}
        assert "devices" not in resources
        assert resources["capacity"]["per_allocation"]
        assert Decimal(options["initial"]["resource_limits"]["capacity"]) == 400
        assert Decimal(options["initial"]["per_allocation_limits"]["capacity"]) == 150
    assert not ResourceQuota.objects.filter(resource_id="devices").exists()
    for save, identifier in [(save_group, {"group_id": user.profile.group_id}),
                              (save_member, {"member_id": user.pk})]:
        with pytest.raises(ValidationError) as error:
            save(user, {"resource_limits": {"devices": 8}}, **identifier)
        assert error.value.code == "resource_unavailable"


def test_derived_quota_applies_to_creation_and_aggregate_reservations(converted_quota):
    user, first, report, values = converted_quota
    values["resource_quantities"]["devices"] = 2
    with pytest.raises(ValidationError) as error:
        submit_task(user, values)
    assert error.value.code == "resource_quota_exceeded"
    values["resource_quantities"]["devices"] = 1
    assert submit_task(user, values)["resource_quantities"]["devices"] == "1"
    second = ComputeNode.objects.create(name="worker", state="online")
    register_capabilities(second.pk, report.model_copy(update={"node_id": second.name}))
    second.refresh_from_db()
    reserve_resources(user, first, "first", {"devices": 1})
    with pytest.raises(ValidationError) as error:
        reserve_resources(user, second, "second", {"devices": 1})
    assert error.value.code == "resource_quota_exceeded"
    assert ResourceReservation.objects.count() == 1
    release_resources(user, "first")
    reserve_resources(user, second, "second", {"devices": 1})


def test_member_override_group_inheritance_and_unlimited_capacity(converted_quota):
    user, node, _, _ = converted_quota
    rule = quota_rules(node)
    assert resource_limit(effective_policy(user), "devices", rules=rule) == 1
    quota = ResourceQuota.objects.get(group=user.profile.group, resource_id="capacity")
    quota.per_allocation_limit = 250
    quota.save(update_fields=["per_allocation_limit"])
    assert resource_limit(effective_policy(user), "devices", rules=rule) == 2
    user.profile.resource_limits = {"per_allocation": {"capacity": "350"}}
    user.profile.save(update_fields=["resource_limits"])
    assert resource_limit(effective_policy(user), "devices", rules=rule) == 3
    user.profile.resource_limits = {"unlimited": True}
    user.profile.save(update_fields=["resource_limits"])
    assert resource_limit(effective_policy(user), "devices", rules=rule) is None
    reserve_resources(user, node, "all", {"devices": 8})
    with pytest.raises(ValidationError) as error:
        reserve_resources(user, node, "excess", {"devices": 9})
    assert error.value.code == "node_capacity_exceeded"


def test_explicit_unlimited_source_and_missing_source_have_distinct_limits(converted_quota):
    user, node, _, _ = converted_quota
    policy = effective_policy(user)
    policy.per_allocation["capacity"] = None
    assert resource_limit(policy, "devices", rules=quota_rules(node)) is None
    policy.per_allocation.clear()
    assert resource_limit(policy, "devices", rules=quota_rules(node)) == Decimal(0)


def test_conflicting_conversion_cannot_corrupt_other_nodes_or_policy_catalog(converted_quota):
    user, node, report, _ = converted_quota
    other = ComputeNode.objects.create(name="conflict", state="online")
    changed = CapabilityReport.model_validate(report.model_dump())
    changed.node_id = other.name
    changed.modules[0].quota_rules["devices"].divisor = 50
    with pytest.raises(ValidationError) as error:
        register_capabilities(other.pk, changed)
    assert error.value.code == "resource_quota_rule_conflict"
    assert not other.resources.exists()
    assert resource_limit(effective_policy(user), "devices", rules=quota_rules(node)) == 1


def test_ascend_conversion_works_alone_and_shares_existing_gpu_policy_metadata():
    from importlib.metadata import entry_points

    modules = {name: list(entry_points(group="gpu_platform.slots.gpu", name=name))[0].load()
               for name in ["ascend", "nvidia-mps"]}
    user = get_user_model().objects.create_user("npu-owner", is_staff=True)
    node = ComputeNode.objects.create(name="ascend", state="online")
    register_capabilities(node.pk, CapabilityReport(node_id=node.name, architecture="arm64",
        modules=[modules["ascend"].descriptor], resources=ResourceUsage(total={"npu_count": 8})))
    node.refresh_from_db()
    assert "gpu_compute" in {row["key"] for row in group_options(user)["resources"]}
    ResourceQuota.objects.create(group=user.profile.group, resource_id="gpu_compute", limit=400,
                                  per_allocation_limit=150)
    policy = effective_policy(user)
    assert resource_limit(policy, "npu_count", rules=quota_rules(node)) == 1
    reserve_resources(user, node, "one", {"npu_count": 1})
    with pytest.raises(ValidationError) as error:
        reserve_resources(user, node, "two", {"npu_count": 1})
    assert error.value.code == "resource_quota_exceeded"
    other = ComputeNode.objects.create(name="nvidia", state="online")
    register_capabilities(other.pk, CapabilityReport(node_id=other.name, architecture="amd64",
        modules=[modules["nvidia-mps"].descriptor], resources=ResourceUsage(total={"gpu_compute": 400, "gpu_memory": 96})))
    assert "npu_count" not in {row["key"] for row in group_options(user)["resources"]}
