from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.paginator import Paginator
from django.test import Client

from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.quotas.models import RegisteredResource, NodeResource, ResourceQuota
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.services import administration
from gpu_platform_control.services.read_models import materialize

pytestmark = pytest.mark.django_db


def test_pagination_preserves_navigation_for_materialized_collections():
    first = materialize(Paginator(list(range(31)), 25).page(1))
    last = materialize(Paginator(list(range(31)), 25).page(2))
    assert first.has_other_pages() and first.has_next() and not first.has_previous()
    assert last.has_other_pages() and last.has_previous() and not last.has_next()
    assert first.next_page_number() == 2 and last.previous_page_number() == 1
    assert list(first) + list(last) == list(range(31))


def test_admin_read_models_keep_current_quotas_and_task_allocations():
    admin = get_user_model().objects.create_user("admin", is_staff=True)
    node = ComputeNode.objects.create(name="main")
    resource = RegisteredResource.objects.create(key="cpu", unit="cores", label={"en": "CPU"})
    NodeResource.objects.create(node=node, resource=resource, total=12, used=2)
    ResourceQuota.objects.create(group=admin.profile.group, resource=resource, limit=Decimal(8))
    result = administration.groups(admin).data["groups"]
    group = next(row for row in result if row["id"] == admin.profile.group_id)
    assert Decimal(next(row for row in group["quota_rows"] if row["key"] == "cpu")["limit"]) == 8
    image = ImageArtifact.objects.create(node=node, name="fixture", reference="fixture:latest")
    request = ResourceRequest.objects.create(image=image, user=admin, node=node, purpose="sample", mode="batch",
        argv=["python", "train.py", "--name", "two words"], resource_quantities={"cpu": "2", "memory": "4", "gpu_compute": "50"},
        volume_sizes={"work": 20}, resource_allocations={"gpu": [{"gpu_compute": "50", "gpu_memory": "2"}]})
    Workspace.objects.create(request=request, allocations={"gpu": [{"device_id": "device-1"}]})
    task = materialize(request)
    assert task["cpu"] == 2 and task["memory_gib"] == 4 and task["workspace_gib"] == 20
    assert task["gpu_rows"][0]["allocation"]["device_id"] == "device-1"
    assert task["gpu_rows"][0]["compute_percent"] == 50
    assert task["command"] == "python train.py --name 'two words'"


def test_ssh_key_submission_keeps_success_feedback_through_redirect():
    user = get_user_model().objects.create_user("key-owner")
    client = Client()
    client.force_login(user)
    from gpu_platform_control.core.accounts.models import SSHKey
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH3ys6jMEUPuJSpGJMBYoQq2XLNudnK1jhOLYa7LxolE fixture"
    response = client.post("/keys/", {"name": "fixture", "public_key": public_key}, follow=True)
    assert response.status_code == 200 and SSHKey.objects.filter(user=user, enabled=True).count() == 1
    assert any(message.level_tag == "success" for message in get_messages(response.wsgi_request))


def test_node_registration_persists_connection_in_cluster_configuration(tmp_path, settings, monkeypatch):
    import json
    import yaml
    from gpu_platform_control.slots.configuration import cluster_config
    from gpu_platform_control.slots.gateway import Gateway
    settings.PLATFORM_CLUSTER_CONFIG = None
    path = tmp_path / "cluster.yaml"
    path.write_text(yaml.safe_dump({"hosts": {"main": {"roles": ["control", "node"]}}}))
    monkeypatch.setenv("PLATFORM_CONFIG", str(path))
    admin = get_user_model().objects.create_user("node-admin", is_staff=True)
    gateway = Gateway()
    result = administration.nodes(admin, values={"name": "new-worker", "architecture": "amd64",
        "address": "127.0.0.1", "transport": "reverse-ssh",
        "transport_options": {"host": "127.0.0.1", "ssh_port": 2229, "identity": "fixture-key"},
        "enabled": True, "maintenance": True})
    assert result.complete
    node = ComputeNode.objects.get(name="new-worker")
    assert node.maintenance and node.enabled
    selection = gateway.configuration.hosts[node.name].slots["transport"]
    assert selection.options["ssh_port"] == 2229
    assert json.loads(cluster_config().model_dump_json())["hosts"][node.name]["architecture"] == "amd64"
    administration.nodes(admin, node.pk, values={"name": node.name, "architecture": "amd64",
        "address": "127.0.0.1", "transport": "reverse-ssh",
        "transport_options": {"host": "127.0.0.1", "ssh_port": 2230, "identity": "fixture-key"},
        "enabled": True, "maintenance": False})
    assert gateway.configuration.hosts[node.name].slots["transport"].options["ssh_port"] == 2230
    assert yaml.safe_load(path.read_text())["hosts"]["main"]["roles"] == ["control", "node"]
    cluster_config.cache_clear()



def test_request_defaults_follow_module_modes_and_batch_allocation_defaults():
    from gpu_platform_control.services.catalog import request_options
    from gpu_platform_control.services.resources import register_capabilities
    from gpu_platform_contracts.messages import CapabilityReport
    from gpu_platform_contracts.module import ModuleDescriptor
    from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage
    from gpu_platform_contracts.texts import LocalizedText
    user = get_user_model().objects.create_user("mode-owner")
    user.profile.resource_limits = {"unlimited": True}
    user.profile.save(update_fields=["resource_limits"])
    node = ComputeNode.objects.create(name="main")
    definitions = [
        ResourceDefinition(key="cpu", unit="cores", label=LocalizedText(en="CPU"), default={"interactive": 2, "batch": 8}),
        ResourceDefinition(key="memory", unit="GiB", label=LocalizedText(en="Memory"), default={"interactive": 4, "batch": 16}),
        ResourceDefinition(key="gpu_compute", unit="percent", label=LocalizedText(en="Compute"),
                           allocation_group="gpu", per_allocation=True, default=100),
        ResourceDefinition(key="gpu_memory", unit="GiB", label=LocalizedText(en="Memory"),
                           allocation_group="gpu", per_allocation=True, default=8),
    ]
    report = CapabilityReport(node_id=node.name, architecture="amd64",
        modules=[ModuleDescriptor(name="fixture", slot="runtime", side="node", version="1", resources=definitions)],
        resources=ResourceUsage(total={"cpu": 32, "memory": 128, "gpu_compute": 100, "gpu_memory": 24}))
    register_capabilities(node.pk, report)
    interactive = request_options(user, node.pk, "interactive")["defaults"]
    batch = request_options(user, node.pk, "batch")["defaults"]
    assert interactive["resource_quantities"] == {"cpu": "2", "memory": "4"}
    assert interactive["resource_allocations"] == {}
    assert batch["resource_quantities"] == {"cpu": "8", "memory": "16"}
    assert batch["resource_allocations"] == {"gpu": [{"gpu_compute": "100", "gpu_memory": "8"}]}



def test_combined_notification_form_retains_secret_and_allows_disabling_without_receiver(settings):
    from copy import deepcopy
    from gpu_platform_control.core.events.models import NotificationSettings
    settings.PLATFORM_CLUSTER_CONFIG = deepcopy(settings.PLATFORM_CLUSTER_CONFIG)
    settings.PLATFORM_CLUSTER_CONFIG["control"] = {"notify": [{"module": "pushplus"}]}
    user = get_user_model().objects.create_user("notify-admin", is_staff=True)
    client = Client()
    client.force_login(user)
    url = "/management/approvals/?tab=push"
    assert client.post(url, {"action": "push", "reminder_minutes": 30}).status_code == 302
    response = client.post(url, {"action": "push", "enabled": "on", "reminder_minutes": 30})
    assert response.status_code == 200 and not NotificationSettings.load().enabled
    response = client.post(url, {"action": "push", "enabled": "on", "reminder_minutes": 30,
                                 "receiver:pushplus:token": "invalid"})
    assert response.status_code == 200 and not NotificationSettings.load().enabled
    token = "0123456789abcdef" * 2
    response = client.post(url, {"action": "push", "enabled": "on", "reminder_minutes": 30,
                                 "receiver:pushplus:token": token})
    assert response.status_code == 302
    configuration = NotificationSettings.load()
    ciphertext = configuration.receivers["pushplus"]["token"]
    assert configuration.enabled and token not in ciphertext
    response = client.post(url, {"action": "push", "enabled": "on", "reminder_minutes": 45,
                                 "receiver:pushplus:token": ""})
    assert response.status_code == 302
    configuration = NotificationSettings.load()
    assert configuration.receivers["pushplus"]["token"] == ciphertext and configuration.reminder_minutes == 45


def test_timeline_keeps_device_rows_and_maps_task_to_its_allocated_device():
    from datetime import timedelta
    from django.utils import timezone
    from gpu_platform_control.core.nodes.models import SystemObservation
    from gpu_platform_control.services.timeline import allocation_timeline
    owner = get_user_model().objects.create_user("timeline-owner")
    node = ComputeNode.objects.create(name="main")
    SystemObservation.objects.create(node=node, name="gpu_inventory",
        data={"devices": [{"uuid": "device-0", "index": 0}, {"uuid": "device-1", "index": 1}]})
    for key, group in [("cpu", None), ("gpu_compute", "gpu")]:
        resource = RegisteredResource.objects.create(key=key, unit="cores" if key == "cpu" else "percent",
            label={"en": key}, allocation_group=group)
        NodeResource.objects.create(node=node, resource=resource, total=100)
    image = ImageArtifact.objects.create(node=node, reference="fixture:latest")
    task = ResourceRequest.objects.create(user=owner, node=node, image=image, mode="batch",
        resource_quantities={"cpu": 2, "gpu_compute": 50},
        resource_allocations={"gpu": [{"gpu_compute": 50, "gpu_memory": 2}]})
    Workspace.objects.create(request=task, started_at=timezone.now()-timedelta(hours=1),
        allocations={"gpu": [{"device_id": "device-1", "compute_percent": 50, "memory_gib": 2}]})
    result = allocation_timeline(owner)
    assert {"device-0", "device-1", f"{node.pk}:cpu"} <= {row["id"] for row in result["rows"]}
    assert f"{node.pk}:gpu_compute" not in {row["id"] for row in result["rows"]}
    assert {row["row"] for row in result["blocks"]} == {"device-1", f"{node.pk}:cpu"}


def test_home_and_physical_storage_are_not_duplicate_member_quotas():
    from django.core.exceptions import ValidationError
    from gpu_platform_control.core.quotas.accounting import normalize_request
    from gpu_platform_control.services.catalog import member_options, group_options
    user = get_user_model().objects.create_user("home-owner", is_staff=True)
    node = ComputeNode.objects.create(name="main")
    for key, kind, requestable in [("storage", None, False), ("home_storage", "home", True),
                                    ("work_storage", "work", True)]:
        resource = RegisteredResource.objects.create(key=key, unit="GiB", label={"en": key},
            accounting="volume", volume_kind=kind, requestable=requestable)
        NodeResource.objects.create(node=node, resource=resource, total=1000)
    with pytest.raises(ValidationError) as error:
        normalize_request(ResourceRequest(user=user, node=node, volume_sizes={"home":50,"work":1}))
    assert error.value.code == "resource_quota_exceeded" and error.value.params["resource"] == "work_storage"
    assert {row["key"] for row in member_options(user, user.pk)["resources"]} == {"work_storage"}
    assert {row["key"] for row in group_options(user, user.profile.group_id)["resources"]} == {"work_storage"}
    assert member_options(user, user.pk)["initial"]["home_size_gib"] == 50


@pytest.mark.parametrize("kind", [None, "home"])
def test_physical_capacity_cannot_be_used_as_a_member_policy(kind):
    from django.core.exceptions import ValidationError
    from gpu_platform_control.services.commands import normalized_policy
    RegisteredResource.objects.create(key="capacity", unit="GiB", label={"en": "Capacity"},
        accounting="volume", requestable=False, volume_kind=kind)
    with pytest.raises(ValidationError):
        normalized_policy({"resource_limits": {"capacity": 100}}, {})


def test_group_home_default_is_inherited_and_members_can_override_it():
    from gpu_platform_control.core.accounts.models import UserGroup
    from gpu_platform_control.services.commands import save_member, save_group
    from gpu_platform_control.services.catalog import member_options
    actor = get_user_model().objects.create_user("group-owner", is_staff=True, is_superuser=True)
    UserGroup.objects.update(is_default=False)
    group = UserGroup.objects.create(name="research", is_default=True, home_size_gib=120)
    automatic = get_user_model().objects.create_user("automatic-owner")
    assert automatic.profile.home_size_gib == 120
    options = member_options(actor)
    assert options["initial"]["home_size_gib"] == 120
    assert options["group_defaults"][str(group.pk)]["home_size_gib"] == 120
    values = {"username": "managed-owner", "password": "Fixture-pass-3187", "group": group.pk}
    member = save_member(actor, values)
    user = get_user_model().objects.get(pk=member["id"])
    assert user.profile.home_size_gib == 120
    save_member(actor, {"group": group.pk, "home_size_gib": 160}, user.pk)
    user.profile.refresh_from_db()
    assert user.profile.home_size_gib == 160
    save_group(actor, {"home_size_gib": 200}, group.pk)
    group.refresh_from_db()
    user.profile.refresh_from_db()
    automatic.profile.refresh_from_db()
    assert group.home_size_gib == 200
    assert user.profile.home_size_gib == 160 and automatic.profile.home_size_gib == 120


@pytest.mark.parametrize("size", [0, -1])
def test_group_home_default_requires_positive_capacity(size):
    from django.core.exceptions import ValidationError
    from gpu_platform_control.core.accounts.models import UserGroup
    from gpu_platform_control.services.commands import save_group
    actor = get_user_model().objects.create_user("group-owner", is_staff=True)
    group = UserGroup.objects.create(name="research")
    with pytest.raises(ValidationError):
        save_group(actor, {"home_size_gib": size}, group.pk)
    group.refresh_from_db()
    assert group.home_size_gib == 50


def test_unlimited_compute_keeps_work_and_image_capacity_limits():
    from django.core.exceptions import ValidationError
    from gpu_platform_control.services.commands import save_group
    from gpu_platform_control.core.quotas.accounting import normalize_request, check_image_quota
    actor = get_user_model().objects.create_user("capacity-admin", is_staff=True)
    node = ComputeNode.objects.create(name="main")
    for key, accounting, kind in [("cpu", "task", None), ("work_storage", "volume", "work"),
                                   ("image_storage", "image", None)]:
        resource = RegisteredResource.objects.create(key=key, unit="GiB", label={"en": key},
            accounting=accounting, volume_kind=kind)
        NodeResource.objects.create(node=node, resource=resource, total=1000)
    save_group(actor, {"unlimited": True, "resource_limits": {"cpu": 8, "work_storage": 20, "image_storage": 5}},
               actor.profile.group_id)
    assert ResourceQuota.objects.get(group_id=actor.profile.group_id, resource_id="cpu").limit is None
    assert ResourceQuota.objects.get(group_id=actor.profile.group_id, resource_id="work_storage").limit == 20
    task = ResourceRequest(user=actor, node=node, volume_sizes={"home":50,"work":20})
    normalize_request(task)
    check_image_quota(actor, node, 5)
    task.volume_sizes["work"] = 21
    with pytest.raises(ValidationError):
        normalize_request(task)
    with pytest.raises(ValidationError):
        check_image_quota(actor, node, 6)
    actor.profile.resource_limits = {"unlimited": True, "resources": {"work_storage": None}}
    actor.profile.save(update_fields=["resource_limits"])
    task.volume_sizes["work"] = 1
    with pytest.raises(ValidationError):
        normalize_request(task)
