from dataclasses import fields, is_dataclass
from collections.abc import Mapping
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Model, QuerySet
from django.forms import BaseForm
from django.test import Client
from gpu_platform_control.services import pages, administration
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact
from django.utils import timezone
from datetime import timedelta

pytestmark = pytest.mark.django_db


def assert_materialized(value):
    assert not isinstance(value, (Model, QuerySet, BaseForm))
    if is_dataclass(value):
        for field in fields(value):
            assert_materialized(getattr(value, field.name))
    elif isinstance(value, Mapping):
        assert 'password' not in value
        assert 'registry_secret' not in value
        for item in value.values():
            assert_materialized(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_materialized(item)


def test_service_reads_materialize_relations_and_validate_access():
    owner = get_user_model().objects.create_user('owner')
    other = get_user_model().objects.create_user('other')
    node = ComputeNode.objects.create(name='node')
    image = ImageArtifact.objects.create(name='image', node=node, owner=owner, reference='image', verified=True)
    task = ResourceRequest.objects.create(user=owner, node=node, image=image, mode='interactive',
        starts_at=timezone.now(), ends_at=timezone.now()+timedelta(hours=1), purpose='work')
    result = pages.task(owner, task.pk)
    assert result.data['task']['id'] == task.pk
    assert result.data['task']['user']['id'] == owner.pk
    assert_materialized(result)
    from gpu_platform_control.services.boundary import NotFound
    with pytest.raises(NotFound):
        pages.task(other, task.pk)
    with pytest.raises(PermissionDenied):
        administration.members(owner)


@pytest.mark.parametrize('route', ['/', '/keys/', '/tasks/', '/images/', '/api/', '/account/notifications/', '/environments/new/'])
def test_user_pages_render_materialized_data(route):
    user = get_user_model().objects.create_user('owner')
    client = Client()
    client.force_login(user)
    assert client.get(route).status_code == 200


@pytest.mark.parametrize('route', ['/management/approvals/', '/management/nodes/', '/management/performance/', '/management/performance/history/', '/management/usage/', '/management/usage/data/', '/management/usage/history/', '/management/containers/', '/management/members/', '/management/members/new/', '/management/groups/', '/management/groups/new/', '/management/storage/', '/management/images/', '/management/settings/', '/management/audits/'])
def test_staff_pages_render_materialized_data(route):
    user = get_user_model().objects.create_user('admin', is_staff=True, is_superuser=True)
    client = Client()
    client.force_login(user)
    assert client.get(route).status_code == 200


def test_panel_creates_token_and_enforces_action_methods():
    user = get_user_model().objects.create_user('owner')
    client = Client()
    client.force_login(user)
    assert client.post('/api/', {'name': 'client', 'expires_in': 86400}).status_code == 302
    result = pages.api_access(user)
    assert len(result.data['tokens']) == 1
    token_id = result.data['tokens'][0]['id']
    assert client.get(f'/api/tokens/{token_id}/delete/').status_code == 405
    assert client.post(f'/api/tokens/{token_id}/delete/').status_code == 302
    assert not pages.api_access(user).data['tokens']


def test_entry_returned_address_controls_connection_for_custom_entry():
    from types import SimpleNamespace
    from uuid import uuid4
    from gpu_platform_control.services.connections import connection_for
    task = SimpleNamespace(pk=uuid4(), status='running', workspace=SimpleNamespace(
        ssh_port=None, connection={'task_id':'task', 'host':'entry.example.test', 'port':2222, 'username':'mapped-user'}))
    connection = connection_for(task)
    assert connection['host'] == 'entry.example.test'
    assert connection['port'] == 2222
    assert connection['user'] == 'mapped-user'
    task.status = 'succeeded'
    assert connection_for(task) is None


def test_dataset_browser_filters_disabled_features_before_rpc(monkeypatch):
    from django.core.exceptions import ValidationError
    from gpu_platform_control.services import datasets
    from gpu_platform_control.services.navigation import features

    owner = get_user_model().objects.create_user('owner')
    disabled = ComputeNode.objects.create(name='disabled', capabilities={'features': {'public_datasets': False}})
    enabled = ComputeNode.objects.create(name='enabled', capabilities={'features': {'public_datasets': True}})
    calls = []
    monkeypatch.setattr(datasets, 'dataset_content', lambda relative, query, node: calls.append(node.pk) or [])
    with pytest.raises(ValidationError) as error:
        datasets.browse(owner, node_id=disabled.pk)
    assert error.value.code == 'feature_disabled' and not calls
    result = datasets.browse(owner)
    assert calls == [enabled.pk]
    assert [node['id'] for node in result['nodes']] == [enabled.pk]
    assert features(owner)['public_datasets']
    enabled.enabled = False
    enabled.save()
    assert not features(owner)['public_datasets']
    client = Client()
    client.force_login(owner)
    assert client.get('/datasets/', {'node': disabled.pk}).status_code == 400
    assert calls == [enabled.pk]
