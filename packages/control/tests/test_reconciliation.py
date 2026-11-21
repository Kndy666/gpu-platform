from datetime import timedelta
from types import SimpleNamespace
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from gpu_platform_contracts.messages import CapabilityReport, NodeReport, Operation, TaskStatus
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_control.core.nodes.models import ComputeNode, StoragePool
from gpu_platform_control.core.retention.models import ImageArtifact, VolumeAllocation
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.core.quotas.models import ResourceReservation
from gpu_platform_control.core.accounts.models import SSHKey
from gpu_platform_control.loop.reconcile import Reconciler
from gpu_platform_control.services.resources import register_capabilities

pytestmark = pytest.mark.django_db


class Gateway:
    def __init__(self, report):
        self.report = report
        self.tasks = {}
        self.started = []
        self.commands = []
        self.offline = False
        self.uncertain_start = False

    def module(self, node, slot):
        return SimpleNamespace(requires_public_port=True)

    def request(self, node, operation, **payload):
        self.commands.append((operation, payload))
        if self.offline:
            raise ConnectionError()
        if operation == Operation.CAPABILITIES:
            return self.report.model_dump(mode='json')
        if operation == Operation.STATUS:
            return NodeReport(node_id=node.name, observed_at=timezone.now(), tasks=list(self.tasks.values())).model_dump(mode='json')
        if operation == Operation.STORAGE_PREPARE:
            return {'id': payload['request']['id'], 'size_gib': payload['request']['size_gib'], 'location': 'volume-' + payload['request']['id']}
        if operation == Operation.RESOURCE_ASSESS:
            return {'feasible': True, 'quantities': payload['resources'], 'gpus': payload['gpus']}
        if operation == Operation.TASK_START:
            self.started.append(payload)
            self.tasks[payload['id']] = TaskStatus(id=payload['id'], state='running', started_at=timezone.now(), port=payload['login_port'])
            if self.uncertain_start:
                self.uncertain_start = False
                raise ConnectionError()
            return self.tasks[payload['id']].model_dump(mode='json')
        if operation == Operation.TASK_STOP:
            self.tasks[payload['task_id']] = TaskStatus(id=payload['task_id'], state='ended', ended_at=timezone.now())
            return self.tasks[payload['task_id']].model_dump(mode='json')
        if operation in {Operation.TASK_KEYS, Operation.TASK_ACK}:
            return {}
        raise AssertionError(operation)


@pytest.fixture
def execution():
    user = get_user_model().objects.create_user('worker')
    user.profile.resource_limits['resources'] = {'cpu': '8', 'memory': '12'}
    user.profile.save(update_fields=['resource_limits'])
    node = ComputeNode.objects.create(name='main', state='online')
    report = CapabilityReport(node_id=node.name, architecture='amd64', modules=[
        ModuleDescriptor(name='runner', slot='runtime', side='node', version='1', resources=[
            ResourceDefinition(key='cpu', unit='cores', label=LocalizedText(en='CPU')),
            ResourceDefinition(key='memory', unit='GiB', label=LocalizedText(en='Memory')),
            ResourceDefinition(key='storage', unit='GiB', accounting='volume', requestable=False, label=LocalizedText(en='Storage'))])],
        resources=ResourceUsage(total={'cpu': 16, 'memory': 32, 'storage': 200}, pools=[{'name': 'storage', 'capacity_gib': 200, 'free_gib': 200, 'reserved_gib': 0}]))
    register_capabilities(node.pk, report)
    image = ImageArtifact.objects.create(node=node, name='base', reference='registry/base:latest',
        node_image_id='base', node_image_owner='shared', verified=True, shared=True, architecture='amd64')
    request = ResourceRequest.objects.create(node=node, user=user, image=image, purpose='example', mode='interactive',
        starts_at=timezone.now()-timedelta(minutes=1), ends_at=timezone.now()+timedelta(hours=1),
        resource_quantities={'cpu': '2', 'memory': '4'}, volume_sizes={'home': 20, 'work': 20}, status='approved')
    pool = StoragePool.objects.get(node=node, name='storage')
    VolumeAllocation.objects.create(node=node, user=user, kind='home', pool=pool, size_gib=20, location='home')
    VolumeAllocation.objects.create(node=node, user=user, request=request, kind='work', pool=pool, size_gib=20, location='work')
    Workspace.objects.create(request=request)
    gateway = Gateway(report)
    return user, node, request, gateway


def test_dispatch_sends_intent_and_commits_reported_state(execution):
    user, node, request, gateway = execution
    Reconciler(node, gateway).tick()
    request.refresh_from_db()
    workspace = Workspace.objects.get(request=request)
    assert request.status == 'running'
    assert workspace.node_port == workspace.ssh_port
    assert gateway.started[0]['owner'] == str(user.pk)
    assert gateway.started[0]['image_owner'] == 'shared'
    assert gateway.started[0]['resources'] == {'cpu': '2', 'memory': '4'}
    assert len(gateway.started[0]['volumes']) == 2
    assert ResourceReservation.objects.count() == 2


def test_rejected_build_does_not_block_task_dispatch(execution):
    from gpu_platform_contracts.texts import Message
    from gpu_platform_control.core.retention.models import ImageBuild
    from gpu_platform_control.slots.gateway import NodeCommandError

    user, node, request, gateway = execution
    build = ImageBuild.objects.create(user=user, node=node, name="rejected", dockerfile="FROM scratch")
    original = gateway.request

    def dispatch(node, operation, **payload):
        if operation == Operation.IMAGE_BUILD:
            raise NodeCommandError(Message(code="command.invalid"))
        return original(node, operation, **payload)

    gateway.request = dispatch
    Reconciler(node, gateway).tick()
    build.refresh_from_db()
    request.refresh_from_db()
    assert build.status == "failed"
    assert request.status == "running"


def test_uncertain_delivery_reconciles_report_without_duplicate_start(execution):
    user, node, request, gateway = execution
    gateway.uncertain_start = True
    reconciler = Reconciler(node, gateway)
    reconciler.tick()
    node.refresh_from_db()
    request.refresh_from_db()
    assert node.state == 'offline'
    assert request.status == 'approved'
    assert request.workspace.dispatch_payload
    reconciler.tick()
    request.refresh_from_db()
    assert request.status == 'running'
    assert len(gateway.started) == 1


def test_offline_node_retains_tasks_ports_and_reservations(execution):
    user, node, request, gateway = execution
    reconciler = Reconciler(node, gateway)
    reconciler.tick()
    original_port = Workspace.objects.get(request=request).ssh_port
    gateway.offline = True
    reconciler.tick()
    request.refresh_from_db()
    workspace = Workspace.objects.get(request=request)
    assert request.status == 'running'
    assert workspace.ssh_port == original_port
    assert not workspace.cleanup_complete
    assert ResourceReservation.objects.count() == 2


def test_confirmed_stop_releases_port_and_quota(execution, django_capture_on_commit_callbacks):
    user, node, request, gateway = execution
    reconciler = Reconciler(node, gateway)
    reconciler.tick()
    Workspace.objects.filter(request=request).update(stop_requested=True)
    with django_capture_on_commit_callbacks(execute=True):
        reconciler.tick()
    request.refresh_from_db()
    workspace = Workspace.objects.get(request=request)
    assert request.status == 'canceled'
    assert workspace.ssh_port is None
    assert workspace.cleanup_complete
    assert not ResourceReservation.objects.exists()
    assert VolumeAllocation.objects.get(request=request).retain_until is not None


def test_key_replacement_includes_active_administrators(execution):
    user, node, request, gateway = execution
    reconciler = Reconciler(node, gateway)
    reconciler.tick()
    administrator = get_user_model().objects.create_user('admin', is_staff=True)
    SSHKey.objects.create(user=administrator, name='access', public_key='ssh-ed25519 key', fingerprint='fingerprint')
    reconciler.tick()
    assert gateway.commands[-1] == (Operation.TASK_KEYS, {'task_id': str(request.pk), 'keys': ['ssh-ed25519 key']})
    SSHKey.objects.filter(user=administrator).update(enabled=False)
    reconciler.tick()
    assert gateway.commands[-1][1]['keys'] == []


def test_busy_assessment_keeps_task_queued_without_reserving_resources(execution):
    user, node, request, gateway = execution
    original = gateway.request
    def dispatch(node, operation, **payload):
        if operation == Operation.RESOURCE_ASSESS:
            return {'feasible': False, 'reason': {'code': 'capacity_busy', 'parameters': {}}}
        return original(node, operation, **payload)
    gateway.request = dispatch
    Reconciler(node, gateway).tick()
    request.refresh_from_db()
    workspace = Workspace.objects.get(request=request)
    assert request.status == 'approved'
    assert workspace.ssh_port is None
    assert workspace.dispatch_started_at is None
    assert not workspace.dispatch_payload
    assert not ResourceReservation.objects.exists()
    assert not gateway.started


def test_dispatch_selects_current_home_after_an_earlier_home_was_deleted(execution):
    user, node, request, gateway = execution
    home = VolumeAllocation.objects.get(node=node, user=user, kind='home')
    VolumeAllocation.objects.create(node=node, user=user, kind='home', pool=home.pool,
        size_gib=home.size_gib, state='deleted')
    Reconciler(node, gateway).tick()
    request.refresh_from_db()
    assert request.status == 'running'
    assert gateway.started[0]['volumes'][0]['id'] == str(home.pk)


def test_partial_node_report_does_not_repeat_offline_transition(execution):
    from gpu_platform_control.core.audit.models import AuditLog
    user, node, request, gateway = execution
    original = gateway.request
    def dispatch(node, operation, **payload):
        if operation == Operation.STATUS:
            raise ConnectionError()
        return original(node, operation, **payload)
    gateway.request = dispatch
    reconciler = Reconciler(node, gateway)
    reconciler.tick()
    reconciler.tick()
    node.refresh_from_db()
    assert node.state == 'offline'
    assert AuditLog.objects.filter(action='node_offline', target=str(node.pk)).count() == 1


def test_terminal_reports_are_acknowledged_after_commit_and_safe_to_repeat(execution, django_capture_on_commit_callbacks):
    from gpu_platform_control.core.audit.models import AuditLog
    user, node, request, gateway = execution
    reconciler = Reconciler(node, gateway)
    reconciler.tick()
    Workspace.objects.filter(request=request).update(stop_requested=True)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        reconciler.tick()
        assert not any(operation == Operation.TASK_ACK for operation, _ in gateway.commands)
    assert callbacks
    assert gateway.commands[-1] == (Operation.TASK_ACK, {'ids': [str(request.pk)]})
    with django_capture_on_commit_callbacks(execute=True):
        reconciler.tick()
    assert len([operation for operation, _ in gateway.commands if operation == Operation.TASK_ACK]) == 2
    assert AuditLog.objects.filter(action='finished', target=str(request.pk)).count() == 1
    assert not ResourceReservation.objects.exists()
