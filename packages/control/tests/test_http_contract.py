from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client

from gpu_platform_contracts.messages import NodeReport
from gpu_platform_control.core.api_auth import issue_token
from gpu_platform_control.core.performance import record_performance
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.nodes.models import SystemObservation
from gpu_platform_control.core.quotas.models import NodeResource, RegisteredResource
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.sync.models import SyncJob

pytestmark = pytest.mark.django_db


@pytest.fixture
def authenticated():
    user = get_user_model().objects.create_user("api-member", password="test-passphrase")
    token = issue_token(user)["token"]
    return Client(HTTP_AUTHORIZATION="Bearer " + token), user


def test_authentication_precedes_body_validation():
    response = Client().post("/api/v1/tasks", data={"unexpected": True}, content_type="application/json")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert not ResourceRequest.objects.exists()


@pytest.mark.parametrize(
    "values",
    [
        {"purpose": "experiment", "mode": "unknown"},
        {"purpose": "experiment", "gpu_requests": [{"compute_percent": 101, "memory_gib": 1}]},
        {"purpose": "experiment", "gpu_requests": [{"compute_percent": 50, "memory_gib": 0}]},
        {"purpose": "experiment", "gpu_requests": [{"compute_percent": 50, "memory_gib": 1.0001}]},
        {"purpose": "experiment", "cpu": 0},
        {"purpose": "experiment", "unexpected": True},
    ],
)
def test_invalid_resource_input_has_no_mutation(authenticated, values):
    client, _ = authenticated
    response = client.post("/api/v1/tasks", data=values, content_type="application/json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["fields"]
    assert not ResourceRequest.objects.exists()


def test_declared_token_limits_apply_before_credential_issuance():
    get_user_model().objects.create_user("api-member", password="test-passphrase")
    response = Client().post(
        "/api/v1/auth/token",
        data={"username": "api-member", "password": "test-passphrase", "expires_in": -1},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert not Session.objects.exists()


def test_typed_wrapper_applies_permission_and_revocation(authenticated):
    client, user = authenticated
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == user.pk
    assert client.get("/api/v1/admin/members").status_code == 403
    assert client.delete("/api/v1/auth/token").status_code == 200
    assert client.get("/api/v1/me").status_code == 401


def test_language_selection_validates_and_persists(authenticated):
    client, user = authenticated
    assert (
        client.patch(
            "/api/v1/me/language", data={"language": "fr"}, content_type="application/json"
        ).status_code
        == 400
    )
    response = client.patch("/api/v1/me/language", data={"language": "en"}, content_type="application/json")
    assert response.status_code == 200
    user.profile.refresh_from_db()
    assert user.profile.language == "en"


def test_image_sync_route_reserves_one_copy_for_repeated_operation(authenticated):
    client, user = authenticated
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    capabilities = {"features": {"image_sync": True}}
    source = ComputeNode.objects.create(name="source", state="online", capabilities=capabilities)
    target = ComputeNode.objects.create(name="target", state="online", capabilities=capabilities)
    image = ImageArtifact.objects.create(node=source, owner=user, name="sample", reference="registry/sample",
                                          architecture="amd64", verified=True, size_bytes=1024)
    options = {"data": {"target": target.pk}, "content_type": "application/json", "HTTP_IDEMPOTENCY_KEY": "copy-request"}
    first = client.post(f"/api/v1/images/{image.pk}/sync", **options)
    second = client.post(f"/api/v1/images/{image.pk}/sync", **options)
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert SyncJob.objects.count() == 1
    listing = client.get("/api/v1/syncs")
    assert listing.status_code == 200
    assert listing.json()["data"]["count"] == 1


@pytest.mark.parametrize("query", [{"page_size": 101}, {"node": 0}, {"kind": "unknown"}, {"kind": "temporary"}])
def test_query_filters_validate_before_listing(authenticated, query):
    client, _ = authenticated
    response = client.get("/api/v1/images", query)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("filters", [{}, {"available": True}, {"kind": "permanent"}])
def test_query_pagination_is_applied_to_permanent_images(authenticated, filters):
    client, user = authenticated
    node = ComputeNode.objects.create(name="catalog-node")
    for index in range(3):
        ImageArtifact.objects.create(node=node, owner=user, name=f"image-{index}", reference=f"registry/{index}", verified=True)
    ImageArtifact.objects.create(
        node=node, owner=user, name="temporary", reference="registry/temporary", verified=True, temporary=True
    )
    response = client.get("/api/v1/images", {"page": 2, "page_size": 2, **filters})
    assert response.status_code == 200
    assert response.json()["data"]["page"] == 2
    assert response.json()["data"]["count"] == 3
    assert len(response.json()["data"]["items"]) == 1


def test_gpu_observations_preserve_fractional_memory_and_module_extensions(authenticated):
    client, user = authenticated
    user.profile.resource_limits = {'unlimited': True}
    user.profile.save(update_fields=['resource_limits'])
    node = ComputeNode.objects.create(name='observed-node', state='online')
    SystemObservation.objects.create(node=node, name='resources', data={})
    SystemObservation.objects.create(node=node, name='gpu_inventory', data={'devices': [{
        'uuid': 'GPU-test', 'index': 0, 'name': 'Test device', 'memory_mib': 24564,
        'used_compute_percent': 25, 'used_memory_mib': 1024,
        'sm_count': 80, 'used_sm_count': 20, 'extensions': {'temperature': 42},
    }]})
    response = client.get('/api/v1/gpus', {'node': node.pk})
    assert response.status_code == 200
    data = response.json()['data']
    assert data['request_limits']['memory_per_card_gib'] == 23.98828125
    assert data['request_limits']['devices'] == [{'memory_gib': 23.98828125}]
    device = data['devices'][0]
    assert device['memory_mib'] == 24564
    assert device['available_compute_percent'] == 75
    assert device['extensions'] == {'sm_count': 80, 'used_sm_count': 20, 'temperature': 42}
    assert 'sm_count' not in device
    resources = client.get('/api/v1/resources', {'node': node.pk})
    assert resources.status_code == 200
    resource_data = resources.json()['data']
    assert resource_data['gpus'][0]['extensions'] == device['extensions']
    assert resource_data['nodes'][0]['gpus'][0]['extensions'] == device['extensions']


def test_capacity_uses_the_requested_node(authenticated):
    client, _ = authenticated
    resource = RegisteredResource.objects.create(key="licenses", unit="seat", label={"en": "Licenses"})
    for name, total in [("smaller", 4), ("larger", 20)]:
        node = ComputeNode.objects.create(name=name)
        NodeResource.objects.create(node=node, resource=resource, total=total)
        response = client.get("/api/v1/resources", {"node": node.pk})
        assert response.status_code == 200
        available = response.json()["data"]["request_capacity"]["resources"]
        assert {row["key"]: float(row["total"]) for row in available} == {"licenses": total}
        data = response.json()["data"]
        assert data["nodes"][0]["display_resources"][0]["key"] == "licenses"
        assert data["running_count"] == data["queued_count"] == 0 and data["accelerator_counts"] == []


def test_live_metrics_include_standard_and_module_readings(authenticated):
    client, user = authenticated
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    node = ComputeNode.objects.create(name="observed", state="online")
    values = {"cpu.percent": 55.5, "cpu.voltage": 1.1, "sensor.reading": 12.5}
    from django.utils import timezone
    record_performance(node, NodeReport(node_id=node.name, observed_at=timezone.now(), metrics=values))
    response = client.get("/api/v1/admin/performance", {"node": node.pk})
    assert response.status_code == 200
    metrics = response.json()["data"]["nodes"][0]["metrics"]
    assert metrics["cpu"]["percent"] == 55.5
    assert metrics["cpu"]["extensions"] == {"voltage": 1.1}
    assert metrics["extensions"] == {"sensor": {"reading": 12.5}, "series": values}


def test_synchronization_listing_is_available_but_public_data_requires_admin(authenticated):
    client, _ = authenticated
    assert client.get('/api/v1/syncs').status_code == 200
    response = client.post('/api/v1/images/00000000-0000-0000-0000-000000000001/sync',
                           data={'target': 1}, content_type='application/json')
    assert response.status_code == 404
    response = client.post('/api/v1/admin/datasets/sync', data={'source': 1, 'target': 2},
                           content_type='application/json')
    assert response.status_code == 403


@pytest.mark.parametrize("used_bytes", [None, 0, 1024])
def test_volume_usage_contract_preserves_unknown_and_measured_values(authenticated, used_bytes):
    from django.utils import timezone
    from gpu_platform_control.core.nodes.models import StoragePool
    from gpu_platform_control.core.retention.models import VolumeAllocation

    client, user = authenticated
    node = ComputeNode.objects.create(name="storage-node")
    pool = StoragePool.objects.create(node=node, name="data")
    image = ImageArtifact.objects.create(node=node, owner=user, name="base", reference="base", verified=True)
    starts_at = timezone.now()
    task = ResourceRequest.objects.create(node=node, user=user, image=image, purpose="work", mode="interactive",
        starts_at=starts_at, ends_at=starts_at + timedelta(hours=1))
    volume = VolumeAllocation.objects.create(node=node, user=user, request=task, kind="work", pool=pool,
                                             size_gib=10, used_bytes=used_bytes)
    response = client.get("/api/v1/volumes")
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["used_bytes"] == used_bytes
    response = client.get(f"/api/v1/tasks/{task.pk}")
    assert response.status_code == 200
    assert response.json()["data"]["volume"]["id"] == str(volume.pk)
    assert response.json()["data"]["volume"]["used_bytes"] == used_bytes
