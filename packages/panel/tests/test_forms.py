from datetime import timedelta
from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from gpu_platform_contracts.messages import CapabilityReport
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_control.core.accounts.models import SSHKey
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.quotas.models import ResourceQuota
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.services.resources import register_capabilities
from gpu_platform_panel.forms import RequestForm, MemberForm, ReceiverForm


def options():
    return {
        "nodes": [{"id": 7, "name": "worker"}],
        "images": [{"id": "image", "name": "image"}],
        "defaults": {"volume_sizes": {"home": 5, "work": 20}},
        "resources": [
            {"key": "licenses", "unit": "seat", "label_text": "Seats", "integral": True, "minimum": 1},
            {
                "key": "scratch",
                "unit": "GiB",
                "accounting": "volume",
                "volume_kind": "work",
                "integral": True,
            },
            {"key": "home", "unit": "GiB", "accounting": "volume", "volume_kind": "home", "integral": True},
            {"key": "accelerator_memory", "unit": "GiB", "allocation_group": "accelerators", "minimum": 1},
        ],
    }


def test_request_form_coerces_arbitrary_resource_intent_and_preserves_home_policy():
    form = RequestForm(
        {
            "node": "7",
            "purpose": "work",
            "image": "image",
            "source_kind": "image",
            "working_dir": "/workspace",
            "command": 'python train.py --name "two words"',
            "variables": "THREADS=4\nLABEL=two words",
            "resource:licenses": "3",
            "volume:work": "25",
            "volume:home": "999",
            "allocations:accelerators": '[{"accelerator_memory":"max"}]',
        },
        options=options(),
        mode="batch",
    )
    assert form.is_valid(), form.errors
    values = form.payload()
    assert values["resource_quantities"] == {"licenses": 3}
    assert values["volume_sizes"] == {"home": 5, "work": 25}
    assert values["resource_allocations"] == {"accelerators": [{"accelerator_memory": "max"}]}
    assert values["argv"] == ["/bin/bash", "-c", 'python train.py --name "two words"']
    assert values["environment"] == {"THREADS": "4", "LABEL": "two words"}
    assert not {"cpu", "gpu", "memory_gib"} & set(form.fields)


def test_member_editor_uses_service_permissions_and_keeps_secret_fields_empty():
    form = MemberForm(
        options={
            "member": {"id": 4},
            "can_grant_admin": False,
            "groups": [{"id": 1, "name": "group"}],
            "resources": [],
        }
    )
    assert "is_staff" not in form.fields
    assert not form.fields["password"].required
    creating = MemberForm(options={"can_grant_admin": True, "resources": []})
    assert "is_staff" in creating.fields and creating.fields["password"].required
    receiver = ReceiverForm(
        {},
        options={
            "channel": "custom",
            "fields": {"key": {"secret": True, "required": True}},
            "values": {"key": True},
        },
    )
    assert receiver.is_valid()
    assert receiver.payload() == {"channel": "custom", "recipient": {}}
    assert not receiver["key"].value()


@pytest.mark.django_db
def test_panel_submission_persists_generic_resources_and_rejects_over_quota():
    user = get_user_model().objects.create_user("owner")
    user.profile.approval_mode = "manual"
    user.profile.save(update_fields=["approval_mode"])
    node = ComputeNode.objects.create(name="worker", architecture="amd64", state="online")
    definitions = [
        ResourceDefinition(
            key="licenses", unit="seat", label=LocalizedText(en="Seats"), integral=True, minimum=1
        ),
        ResourceDefinition(
            key="work_space",
            unit="GiB",
            label=LocalizedText(en="Work"),
            accounting="volume",
            volume_kind="work",
            integral=True,
            minimum=1,
        ),
        ResourceDefinition(
            key="home_space",
            unit="GiB",
            label=LocalizedText(en="Home"),
            accounting="volume",
            volume_kind="home",
            integral=True,
            minimum=1,
        ),
    ]
    report = CapabilityReport(
        node_id=node.name,
        architecture="amd64",
        modules=[
            ModuleDescriptor(name="custom", slot="runtime", version="1", side="node", resources=definitions)
        ],
        resources=ResourceUsage(total={"licenses": 10, "work_space": 100, "home_space": 100}),
    )
    register_capabilities(node.pk, report)
    for key, limit in [("licenses", 4), ("work_space", 100), ("home_space", 100)]:
        ResourceQuota.objects.create(group=user.profile.group, resource_id=key, limit=limit)
    SSHKey.objects.create(user=user, name="key", public_key="ssh-ed25519 fixture", fingerprint="fixture")
    image = ImageArtifact.objects.create(
        node=node, owner=user, name="environment", reference="fixture", architecture="amd64", verified=True
    )
    client = Client()
    client.force_login(user)
    values = {
        "node": node.pk,
        "purpose": "work",
        "image": image.pk,
        "working_dir": "/workspace",
        "resource:licenses": "3",
        "volume:work": "20",
        "starts_at": timezone.now().isoformat(),
        "ends_at": (timezone.now() + timedelta(hours=1)).isoformat(),
    }
    response = client.post("/environments/new/", values)
    assert response.status_code == 302
    task = ResourceRequest.objects.get(user=user)
    assert Decimal(task.resource_quantities["licenses"]) == 3
    assert task.volume_sizes == {"home": 50, "work": 20}
    values["resource:licenses"] = "5"
    response = client.post("/environments/new/", values)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert ResourceRequest.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_group_editor_saves_arbitrary_total_and_per_allocation_limits():
    from gpu_platform_control.core.accounts.models import UserGroup
    from gpu_platform_control.core.quotas.models import RegisteredResource, NodeResource

    admin = get_user_model().objects.create_user("admin", is_staff=True, is_superuser=True)
    node = ComputeNode.objects.create(name="worker")
    resource = RegisteredResource.objects.create(key="slots", unit="unit", label={"en": "Slots"},
        allocation_group="devices", allocation_field="slots", per_allocation=True)
    NodeResource.objects.create(node=node, resource=resource, total=100)
    client = Client()
    client.force_login(admin)
    response = client.post('/management/groups/new/', {"name": "group", "concurrent_tasks": 2,
        "interactive_hours": 12, "extension_limit": 1, "approval_mode": "manual",
        "resource:slots": "12.5", "allocation_limit:slots": "3.5", "home_size_gib": 50})
    assert response.status_code == 302
    group = UserGroup.objects.get(name="group")
    quota = ResourceQuota.objects.get(group=group, resource=resource)
    assert quota.limit == Decimal("12.5")
    assert quota.per_allocation_limit == Decimal("3.5")


@pytest.mark.django_db
def test_invalid_notification_settings_remain_bound_without_mutation():
    from gpu_platform_control.core.events.models import NotificationSettings

    admin = get_user_model().objects.create_user("admin", is_staff=True)
    initial = NotificationSettings.load()
    client = Client()
    client.force_login(admin)
    response = client.post('/management/approvals/?tab=push', {"action": "push", "reminder_minutes": "0"})
    assert response.status_code == 200
    assert response.context['push_form'].errors
    initial.refresh_from_db()
    assert not initial.enabled
    response = client.post('/management/approvals/?tab=push', {"action": "push", "reminder_minutes": "45"})
    assert response.status_code == 302
    initial.refresh_from_db()
    assert not initial.enabled and initial.reminder_minutes == 45
