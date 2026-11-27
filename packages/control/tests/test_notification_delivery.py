from datetime import timedelta, timezone as datetime_timezone
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_control.core.events.models import Notification, NotificationSettings
from gpu_platform_control.core.notifications import deliver_one, cipher
from gpu_platform_control.core.services import audit
from gpu_platform_control.services.notifications import set_notification_receiver
from gpu_platform_control.slots.configuration import cluster_config

pytestmark = pytest.mark.django_db


class Channel:
    descriptor = ModuleDescriptor(name='mail', slot='notify', side='control', version='1')
    def __init__(self):
        self.calls = []
        self.fail = False
    def recipient_fields(self):
        return {'address': {'required': True}, 'key': {'required': True, 'secret': True}}
    def send(self, recipient, event, language):
        self.calls.append((recipient, event, language))
        if self.fail:
            raise ConnectionError()


@pytest.fixture
def notifications(monkeypatch):
    channel = Channel()
    configuration = {'control': {'notify': [{'module': 'mail'}]}, 'hosts': {'main': {'roles': ['control', 'node']}}}
    with override_settings(PLATFORM_CLUSTER_CONFIG=configuration):
        cluster_config.cache_clear()
        monkeypatch.setattr('gpu_platform_control.services.notifications.load', lambda *args, **kwargs: channel)
        user = get_user_model().objects.create_user('recipient')
        user.profile.language = 'zh-hans'
        user.profile.save(update_fields=['language'])
        NotificationSettings.objects.create(enabled=True, events=['submit'])
        yield user, channel
    cluster_config.cache_clear()


def test_receiver_secrets_are_encrypted_and_events_keep_structured_parameters(notifications):
    user, channel = notifications
    set_notification_receiver(user, 'mail', {'address': 'member@example.invalid', 'key': 'receiver-key'})
    user.profile.refresh_from_db()
    stored = user.profile.notification_receivers['mail']['key']
    assert stored != 'receiver-key'
    assert cipher().decrypt(stored.encode()).decode() == 'receiver-key'
    audit(user, 'submit', 'record-1', quantity=3)
    event = Notification.objects.get()
    assert event.payload == {'code': 'submit', 'parameters': {'target': 'record-1', 'quantity': 3}}
    assert deliver_one(loader=lambda *args, **kwargs: channel)
    event.refresh_from_db()
    assert event.state == 'sent'
    recipient, message, language = channel.calls[0]
    assert recipient == {'address': 'member@example.invalid', 'key': 'receiver-key'}
    assert message.code == 'submit'
    assert message.parameters['quantity'] == 3
    assert language == 'zh-hans'


def test_channel_failure_retries_with_backoff(notifications):
    user, channel = notifications
    set_notification_receiver(user, 'mail', {'address': 'member@example.invalid', 'key': 'receiver-key'})
    audit(user, 'submit', 'record-1')
    channel.fail = True
    clock = timezone.now()
    assert deliver_one(loader=lambda *args, **kwargs: channel, clock=lambda: clock)
    event = Notification.objects.get()
    assert event.state == 'pending'
    assert event.attempts == 1
    assert event.next_attempt_at > clock
    assert not deliver_one(loader=lambda *args, **kwargs: channel, clock=lambda: clock)
    channel.fail = False
    assert deliver_one(loader=lambda *args, **kwargs: channel, clock=lambda: clock + timedelta(minutes=3))
    event.refresh_from_db()
    assert event.state == 'sent'
    assert event.attempts == 2


def test_task_start_report_notifies_owner_once_despite_repeated_reports(notifications):
    from gpu_platform_contracts.messages import TaskStatus
    from gpu_platform_control.core.nodes.models import ComputeNode
    from gpu_platform_control.core.retention.models import ImageArtifact
    from gpu_platform_control.core.requests.models import ResourceRequest
    from gpu_platform_control.core.tasks.models import Workspace
    from gpu_platform_control.loop.reconcile import Reconciler
    user, channel = notifications
    set_notification_receiver(user, 'mail', {'address': 'member@example.invalid', 'key': 'receiver-key'})
    NotificationSettings.objects.update(events=['running'])
    node = ComputeNode.objects.create(name='main')
    image = ImageArtifact.objects.create(node=node, owner=user, name='base', reference='base', verified=True)
    task = ResourceRequest.objects.create(node=node, user=user, image=image, mode='interactive',
        status='approved', purpose='training', starts_at=timezone.now(), ends_at=timezone.now()+timedelta(hours=1))
    Workspace.objects.create(request=task)
    status = TaskStatus(id=str(task.pk), state='running', started_at=timezone.now())
    reconciler = Reconciler(node)
    reconciler.apply_status(status)
    reconciler.apply_status(status)
    notification = Notification.objects.get(event='running')
    assert notification.recipient_id == user.pk
    assert notification.payload['parameters']['target'] == str(task.pk)
    assert deliver_one(loader=lambda *args, **kwargs: channel)
    assert len(channel.calls) == 1
    assert channel.calls[0][1].code == 'running'


def make_task(user, *, mode='batch', status='running', started_at=None):
    from gpu_platform_control.core.nodes.models import ComputeNode
    from gpu_platform_control.core.retention.models import ImageArtifact
    from gpu_platform_control.core.requests.models import ResourceRequest
    from gpu_platform_control.core.tasks.models import Workspace
    node, _ = ComputeNode.objects.get_or_create(name='main')
    image = ImageArtifact.objects.create(node=node, owner=user, name='base', reference='base', verified=True)
    schedule = {'starts_at': timezone.now(), 'ends_at': timezone.now()+timedelta(hours=1)} if mode == 'interactive' else {}
    task = ResourceRequest.objects.create(node=node, user=user, image=image, mode=mode,
        status=status, purpose='training', **schedule)
    Workspace.objects.create(request=task, started_at=started_at)
    return task


def test_offline_transition_notifies_staff_and_affected_owners_once(notifications):
    from gpu_platform_control.core.notifications import mark_node_offline
    from gpu_platform_control.core.nodes.models import ComputeNode
    user, channel = notifications
    task = make_task(user)
    staff = get_user_model().objects.create_user('staff', is_staff=True)
    inactive = get_user_model().objects.create_user('inactive', is_staff=True, is_active=False)
    unrelated = get_user_model().objects.create_user('unrelated')
    NotificationSettings.objects.update(events=['node_offline'])
    mark_node_offline(task.node_id)
    mark_node_offline(task.node_id)
    jobs = Notification.objects.filter(event='node_offline')
    assert set(jobs.values_list('recipient_id', flat=True)) == {user.pk, staff.pk}
    assert jobs.count() == 2
    assert not jobs.filter(recipient__in=[inactive, unrelated]).exists()

    fresh, stale = jobs.order_by('created_at', 'pk')
    deadline = fresh.created_at + timedelta(hours=24)
    assert deliver_one(loader=lambda *args, **kwargs: channel, clock=lambda: deadline - timedelta(seconds=1))
    fresh.refresh_from_db()
    assert fresh.state == 'sent'
    assert fresh.payload['expires_at'] == deadline.astimezone(datetime_timezone.utc).isoformat()
    assert deliver_one(loader=lambda *args, **kwargs: channel, clock=lambda: stale.created_at + timedelta(hours=24))
    stale.refresh_from_db()
    assert stale.state == 'skipped'
    assert len(channel.calls) == 1

    ComputeNode.objects.filter(pk=task.node_id).update(state='online')
    mark_node_offline(task.node_id)
    assert jobs.count() == 4


def test_work_volume_reminder_waits_for_idle_retention_window_and_tracks_deadline(notifications):
    from gpu_platform_control.core.nodes.models import StoragePool
    from gpu_platform_control.core.retention.models import VolumeAllocation
    from gpu_platform_control.core.tasks.models import Workspace
    from gpu_platform_control.core.notifications import collect_retention_reminders
    user, channel = notifications
    now = timezone.now()
    task = make_task(user, status='succeeded')
    Workspace.objects.filter(request=task).update(cleanup_complete=True, ended_at=now)
    pool = StoragePool.objects.create(node=task.node, name='data', capacity_gib=200)
    volume = VolumeAllocation.objects.create(node=task.node, user=user, request=task,
        kind='work', pool=pool, size_gib=10, state='bound', retain_until=now+timedelta(hours=2))
    NotificationSettings.objects.update(events=['volume_deletion_notice'], reminder_minutes=30)
    collect_retention_reminders(clock=lambda: now)
    assert not Notification.objects.exists()
    deadline = now+timedelta(minutes=20)
    VolumeAllocation.objects.filter(pk=volume.pk).update(retain_until=deadline)
    Workspace.objects.filter(request=task).update(cleanup_complete=False)
    collect_retention_reminders(clock=lambda: now)
    assert not Notification.objects.exists()
    Workspace.objects.filter(request=task).update(cleanup_complete=True)
    collect_retention_reminders(clock=lambda: now)
    collect_retention_reminders(clock=lambda: now)
    notification = Notification.objects.get(event='volume_deletion_notice')
    assert notification.recipient_id == user.pk
    assert notification.payload['parameters']['retain_until'] == deadline.isoformat()
    volume.refresh_from_db()
    assert volume.notified_at == now
    VolumeAllocation.objects.filter(pk=volume.pk).update(retain_until=now+timedelta(minutes=25), notified_at=None)
    collect_retention_reminders(clock=lambda: now)
    assert Notification.objects.filter(event='volume_deletion_notice').count() == 2


def test_long_batch_reminder_uses_actual_start_and_skips_stopped_or_interactive_work(notifications):
    from gpu_platform_control.core.tasks.models import Workspace
    from gpu_platform_control.core.notifications import collect_long_batch_reminders
    user, channel = notifications
    now = timezone.now()
    old = now-timedelta(hours=169)
    task = make_task(user, started_at=old)
    make_task(user, started_at=now-timedelta(hours=167))
    make_task(user, mode='interactive', started_at=old)
    stopped = make_task(user, started_at=old)
    Workspace.objects.filter(request=stopped).update(stop_requested=True)
    ended = make_task(user, status='succeeded', started_at=old)
    Workspace.objects.filter(request=ended).update(ended_at=now)
    NotificationSettings.objects.update(events=['long_batch'])
    collect_long_batch_reminders(clock=lambda: now)
    collect_long_batch_reminders(clock=lambda: now+timedelta(minutes=10))
    notification = Notification.objects.get(event='long_batch')
    assert notification.recipient_id == user.pk
    assert notification.payload['parameters']['target'] == str(task.pk)
    assert notification.payload['parameters']['started_at'] == old.isoformat()


def test_node_module_event_is_available_to_notification_settings(notifications):
    from gpu_platform_control.core.nodes.models import ComputeNode
    from gpu_platform_control.core.notifications import event_labels
    from gpu_platform_contracts.messages import CapabilityReport
    from gpu_platform_contracts.texts import LocalizedText
    user, channel = notifications
    descriptor = ModuleDescriptor(name='device', slot='gpu', side='node', version='1',
        events={'device.failure': LocalizedText(en='Device failure', zh_hans='设备故障')})
    capabilities = CapabilityReport(node_id='worker', architecture='amd64', modules=[descriptor])
    ComputeNode.objects.create(name='worker', capabilities=capabilities.model_dump(mode='json'))
    assert 'device.failure' in event_labels(loader=lambda *args, **kwargs: channel)


def test_module_event_receipt_deduplicates_delivery_and_derives_task_owner(notifications):
    from gpu_platform_contracts.events import ModuleEvent
    from gpu_platform_contracts.texts import LocalizedText
    from gpu_platform_control.core.events.ingest import ingest_events
    from gpu_platform_control.core.events.models import EventReceipt
    from gpu_platform_control.core.audit.models import AuditLog
    user, channel = notifications
    task = make_task(user)
    descriptor = ModuleDescriptor(name='meter', slot='gpu', side='node', version='1',
        events={'device.failure': LocalizedText(en='Device failure')})
    task.node.capabilities = {'modules': [descriptor.model_dump(mode='json')]}
    task.node.save(update_fields=['capabilities'])
    NotificationSettings.objects.update(events=['device.failure'])
    event = ModuleEvent(slot='gpu', code='device.failure', task_id=str(task.pk), parameters={'device': 'a'})
    first = ingest_events(task.node, [event])
    second = ingest_events(task.node, [event])
    assert first == second == [str(event.id)]
    assert EventReceipt.objects.count() == 1
    assert AuditLog.objects.filter(action='device.failure').count() == 1
    notification = Notification.objects.get(event='device.failure')
    assert notification.recipient_id == user.pk
    assert notification.payload['parameters']['device'] == 'a'


def test_module_event_cannot_target_task_owned_by_another_node(notifications):
    from gpu_platform_contracts.events import ModuleEvent
    from gpu_platform_control.core.events.ingest import ingest_events
    from gpu_platform_control.core.nodes.models import ComputeNode
    from gpu_platform_control.core.events.models import EventReceipt
    from django.core.exceptions import ValidationError
    user, channel = notifications
    task = make_task(user)
    other = ComputeNode.objects.create(name='other', capabilities={'modules': [{'slot': 'gpu', 'events': {'device.failure': {'en': 'Failure'}}}]})
    with pytest.raises(ValidationError) as error:
        ingest_events(other, [ModuleEvent(slot='gpu', code='device.failure', task_id=str(task.pk))])
    assert error.value.code == 'module_event_task_unknown'
    assert not EventReceipt.objects.exists()
    assert not Notification.objects.exists()


def test_control_module_event_reaches_durable_audit_and_active_administrators(notifications):
    from gpu_platform_contracts.module import Module, ModuleContext
    from gpu_platform_contracts.texts import LocalizedText
    from gpu_platform_control.slots import bind_events
    from gpu_platform_control.core.events.models import EventReceipt
    user, channel = notifications
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    class Provider(Module):
        descriptor = ModuleDescriptor(name='campus', slot='auth', side='control', version='1',
            events={'campus.unavailable': LocalizedText(en='Unavailable')})
    NotificationSettings.objects.update(events=['campus.unavailable'])
    context = ModuleContext()
    bind_events(context, Provider.descriptor)
    event = Provider(context).emit('campus.unavailable', attempt=2)
    receipt = EventReceipt.objects.get()
    assert receipt.event_id == event.id
    assert receipt.source == 'control:auth:campus'
    assert receipt.audit.detail['attempt'] == 2
    assert Notification.objects.get().recipient_id == user.pk
