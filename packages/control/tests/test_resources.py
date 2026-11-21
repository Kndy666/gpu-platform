from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from gpu_platform_contracts.messages import CapabilityReport
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.quotas.models import ResourceQuota, ResourceReservation
from gpu_platform_control.services.resources import register_capabilities, reserve_resources, release_resources, available_resources

pytestmark = pytest.mark.django_db


def inventory(node, total=12, version='1.0', key='licenses'):
    return CapabilityReport(node_id=node.name, architecture='amd64', version=version,
        modules=[ModuleDescriptor(name='meter', slot='runtime', side='node', version='1',
            resources=[ResourceDefinition(key=key, unit='seat', label=LocalizedText(en='Seats'), integral=True)])],
        resources=ResourceUsage(total={key: total}))


@pytest.fixture
def allocation():
    user = get_user_model().objects.create_user('reader')
    first = ComputeNode.objects.create(name='first', state='online')
    second = ComputeNode.objects.create(name='second', state='online')
    register_capabilities(first.pk, inventory(first))
    register_capabilities(second.pk, inventory(second))
    ResourceQuota.objects.create(group=user.profile.group, resource_id='licenses', limit=10)
    return user, first, second


def test_aggregate_quota_reserves_across_nodes_atomically(allocation):
    user, first, second = allocation
    reserve_resources(user, first, 'job-a', {'licenses': 6})
    with pytest.raises(ValidationError) as error:
        reserve_resources(user, second, 'job-b', {'licenses': 5})
    assert error.value.code == 'resource_quota_exceeded'
    assert ResourceReservation.objects.count() == 1
    reserve_resources(user, second, 'job-b', {'licenses': 4})
    assert sum(ResourceReservation.objects.values_list('amount', flat=True)) == Decimal(10)
    release_resources(user, 'job-a')
    reserve_resources(user, second, 'job-b', {'licenses': 8})
    assert ResourceReservation.objects.get(subject='job-b').amount == 8


def test_registration_controls_resource_visibility(allocation):
    user, first, second = allocation
    assert {row['key'] for row in available_resources()} == {'licenses'}
    register_capabilities(first.pk, CapabilityReport(node_id=first.name, architecture='amd64'))
    assert available_resources(first) == []
    assert len(available_resources()) == 1
    register_capabilities(second.pk, CapabilityReport(node_id=second.name, architecture='amd64'))
    assert available_resources() == []


def test_capability_updates_preserve_existing_reservations(allocation):
    user, first, second = allocation
    reserve_resources(user, first, 'job-a', {'licenses': 3})
    register_capabilities(first.pk, inventory(first, version='2.0'))
    first.refresh_from_db()
    assert not first.maintenance
    assert ResourceReservation.objects.get(subject='job-a').amount == 3
    reserve_resources(user, first, 'job-b', {'licenses': 1})


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', -1, '0.25'])
def test_invalid_quantities_do_not_enter_ledger(allocation, amount):
    user, first, _ = allocation
    with pytest.raises(ValidationError):
        reserve_resources(user, first, 'job', {'licenses': amount})
    assert not ResourceReservation.objects.exists()


def test_capacity_accounts_for_other_owners(allocation):
    user, first, _ = allocation
    second = get_user_model().objects.create_user('writer')
    reserve_resources(user, first, 'a', {'licenses': 8})
    with pytest.raises(ValidationError) as error:
        reserve_resources(second, first, 'b', {'licenses': 5})
    assert error.value.code == 'node_capacity_exceeded'


@pytest.fixture
def generic_task():
    from gpu_platform_control.core.accounts.models import SSHKey
    from gpu_platform_control.core.retention.models import ImageArtifact
    user = get_user_model().objects.create_user('task-owner')
    user.profile.approval_mode = 'manual'
    user.profile.save(update_fields=['approval_mode'])
    SSHKey.objects.create(user=user, name='test', public_key='ssh-ed25519 test', fingerprint='key')
    node = ComputeNode.objects.create(name='main', state='online')
    resources = [ResourceDefinition(key='licenses', unit='seat', integral=True, minimum=1,
        label=LocalizedText(en='Seats')),
        ResourceDefinition(key='volume_capacity', unit='GiB', accounting='volume', requestable=False,
            label=LocalizedText(en='Volumes')),
        ResourceDefinition(key='home_space', unit='GiB', accounting='volume', volume_kind='home', integral=True,
            label=LocalizedText(en='Home')),
        ResourceDefinition(key='work_space', unit='GiB', accounting='volume', volume_kind='work', integral=True,
            label=LocalizedText(en='Work'))]
    report = CapabilityReport(node_id='main', architecture='amd64', modules=[
        ModuleDescriptor(name='custom', slot='runtime', side='node', version='1', resources=resources)],
        resources=ResourceUsage(total={'licenses': 12, 'volume_capacity': 300, 'home_space': 300, 'work_space': 300}))
    register_capabilities(node.pk, report)
    for key, amount in [('licenses', 6), ('work_space', 100)]:
        ResourceQuota.objects.create(group=user.profile.group, resource_id=key, limit=amount)
    image = ImageArtifact.objects.create(node=node, shared=True, name='base', reference='base', digest='sha256:'+'a'*64,
        verified=True, architecture='amd64')
    return user, node, {'node': node.pk, 'image': image.pk, 'mode': 'batch', 'purpose': 'run', 'argv': ['true'],
        'resource_quantities': {'licenses': '2'}, 'volume_sizes': {'home': 50, 'work': 20}}


def test_custom_resource_limits_apply_before_request_creation(generic_task):
    from gpu_platform_control.services.commands import submit_task
    from gpu_platform_control.core.requests.models import ResourceRequest
    user, node, values = generic_task
    values['resource_quantities']['licenses'] = '7'
    with pytest.raises(ValidationError) as error:
        submit_task(user, values)
    assert error.value.code == 'resource_quota_exceeded'
    assert not ResourceRequest.objects.exists()
    values['resource_quantities']['licenses'] = '2'
    result = submit_task(user, values)
    task = ResourceRequest.objects.get(pk=result['id'])
    assert task.resource_quantities == {'licenses': '2', 'home_space': '50', 'work_space': '20'}
    assert task.status == 'submitted'


def test_explicit_zero_does_not_bypass_declared_minimum(generic_task):
    from gpu_platform_control.services.commands import submit_task
    user, node, values = generic_task
    values['resource_quantities']['licenses'] = 0
    with pytest.raises(ValidationError) as error:
        submit_task(user, values)
    assert error.value.code == 'resource_amount_invalid'


def test_quota_patch_preserves_other_registered_limits(generic_task):
    from gpu_platform_control.services.commands import save_group
    user, node, values = generic_task
    user.is_staff = user.is_superuser = True
    user.save(update_fields=['is_staff', 'is_superuser'])
    group = user.profile.group
    save_group(user, {'name': 'Changed'}, group_id=group.pk)
    assert ResourceQuota.objects.get(group=group, resource_id='licenses').limit == 6
    save_group(user, {'resource_limits': {'licenses': '4'}}, group_id=group.pk)
    assert ResourceQuota.objects.get(group=group, resource_id='licenses').limit == 4
    assert ResourceQuota.objects.get(group=group, resource_id='work_space').limit == 100


@pytest.mark.parametrize('changes', [{'requestable': False}, {'minimum': 2}, {'maximum': 8}, {'step': 2}, {'accounting': 'image'}])
def test_resource_metadata_conflicts_leave_existing_capacity_unchanged(allocation, changes):
    user, first, second = allocation
    report = inventory(second, total=99)
    definition = report.modules[0].resources[0]
    report.modules[0].resources[0] = type(definition).model_validate(
        {**definition.model_dump(), **changes}
    )
    with pytest.raises(ValidationError) as error:
        register_capabilities(second.pk, report)
    assert error.value.code == 'resource_definition_conflict'
    from gpu_platform_control.core.quotas.models import NodeResource
    assert NodeResource.objects.get(node=second, resource_id='licenses').total == 12


def test_custom_allocation_group_enforces_item_and_total_limits(generic_task):
    from gpu_platform_control.core.quotas.models import RegisteredResource, NodeResource
    from gpu_platform_control.services.commands import submit_task
    from gpu_platform_control.core.requests.models import ResourceRequest
    user, node, values = generic_task
    definition = RegisteredResource.objects.create(key='channels', unit='seat', label={'en': 'Channels'},
        integral=True, minimum=1, allocation_group='rack', per_allocation=True)
    NodeResource.objects.create(node=node, resource=definition, total=12)
    ResourceQuota.objects.create(group=user.profile.group, resource=definition, limit=6, per_allocation_limit=3)
    values['resource_allocations'] = {'rack': [{'channels': 2}, {'channels': 4}]}
    with pytest.raises(ValidationError) as error:
        submit_task(user, values)
    assert error.value.code == 'resource_allocation_quota_exceeded'
    assert not ResourceRequest.objects.exists()
    values['resource_allocations']['rack'][1]['channels'] = 3
    result = submit_task(user, values)
    task = ResourceRequest.objects.get(pk=result['id'])
    assert task.resource_quantities['channels'] == '5'
    assert task.resource_allocations == {'rack': [{'channels': '2'}, {'channels': '3'}]}


def test_api_custom_quota_patch_preserves_unspecified_group_and_member_limits(generic_task):
    from django.test import Client
    from gpu_platform_control.core.api_auth import issue_token
    user, node, values = generic_task
    user.is_staff = user.is_superuser = True
    user.save(update_fields=['is_staff', 'is_superuser'])
    user.profile.resource_limits.update(resources={'licenses': '3'}, concurrent_tasks=2)
    user.profile.save(update_fields=['resource_limits'])
    client = Client(HTTP_AUTHORIZATION='Bearer '+issue_token(user)['token'])
    response = client.patch(f'/api/v1/admin/groups/{user.profile.group_id}',
        data={'name': 'Resource group'}, content_type='application/json')
    assert response.status_code == 200
    assert ResourceQuota.objects.get(group=user.profile.group, resource_id='licenses').limit == 6
    response = client.patch(f'/api/v1/admin/groups/{user.profile.group_id}',
        data={'extensions': {'resources': {'licenses': 5}}}, content_type='application/json')
    assert response.status_code == 200
    assert ResourceQuota.objects.get(group=user.profile.group, resource_id='licenses').limit == 5
    assert ResourceQuota.objects.get(group=user.profile.group, resource_id='work_space').limit == 100
    response = client.patch(f'/api/v1/admin/members/{user.pk}', data={'first_name': 'Reader'}, content_type='application/json')
    assert response.status_code == 200
    assert response.json()['data']['effective_limits']['extensions']['resources']['licenses'] == '3'
    assert response.json()['data']['effective_limits']['concurrent_tasks'] == 2
    user.profile.refresh_from_db()
    assert user.profile.resource_limits['resources']['licenses'] == '3'
