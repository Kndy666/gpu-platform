from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from portal.models import *
from portal.services import approve, reject, request_stop, extend, parse_ssh_key, validate_request, resize_home
from portal.manifests import task_objects
from portal.resources import resource_summary
from tests.factories import fixtures, task

class BusinessTests(TestCase):
    def setUp(self): self.admin, self.a, self.b, self.image = fixtures()

    def test_idempotent_approval(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        approve(req.pk, self.admin)
        self.assertEqual(Workspace.objects.count(), 1)
        self.assertEqual(VolumeAllocation.objects.count(), 2)
        self.assertEqual(StoragePool.objects.get().reserved_gib, 150)
        self.assertEqual(AuditLog.objects.filter(action='approve').count(), 1)

    def test_more_approved_tasks_can_wait_under_native_concurrency_limit(self):
        UserGroup.objects.update(concurrent_tasks=1)
        a = task(self.a, self.image)
        b = task(self.a, self.image)
        approve(a.pk, self.admin)
        approve(b.pk, self.admin)
        self.assertEqual(Workspace.objects.count(), 2)
        from portal.queues import queue_objects
        cluster, local = queue_objects(self.a, UserGroup.objects.get())
        slots = cluster['spec']['resourceGroups'][0]['flavors'][0]['resources'][-1]
        self.assertEqual(slots, {'name': 'pods', 'nominalQuota': '1', 'borrowingLimit': '0', 'lendingLimit': '0'})

    def test_failed_capacity_rolls_back_home_and_request(self):
        pool = StoragePool.objects.get()
        pool.free_gib = 100
        pool.save()
        req = task(self.a, self.image)
        with self.assertRaises(ValidationError): approve(req.pk, self.admin)
        self.assertEqual(VolumeAllocation.objects.count(), 0)
        self.assertEqual(StoragePool.objects.get().reserved_gib, 0)
        self.assertEqual(ResourceRequest.objects.get(pk=req.pk).status, 'submitted')

    def test_stale_home_allocation_cannot_exceed_current_group_limit(self):
        UserGroup.objects.update(home_gib=10)
        req = task(self.a, self.image)
        with self.assertRaisesMessage(ValidationError, '个人卷额度超过当前用户组上限'):
            approve(req.pk, self.admin)
        self.assertFalse(VolumeAllocation.objects.exists())

    def test_no_cross_disk_volume(self):
        StoragePool.objects.update(capacity_gib=200, free_gib=200)
        StoragePool.objects.create(name='other', path='/other', storage_class='rawfile-other', capacity_gib=200,
                                   free_gib=200, healthy=True, observed_at=timezone.now())
        req = task(self.a, self.image, workspace_gib=300)
        with self.assertRaises(ValidationError): approve(req.pk, self.admin)

    def test_batch_ignores_interactive_duration(self):
        req = task(self.a, self.image, mode='batch', starts_at=None, ends_at=None, argv=['python', 'train.py'])
        approve(req.pk, self.admin)
        ws = Workspace.objects.get(request=req)
        ws.ssh_port = 30022
        _, job, _ = task_objects(ResourceRequest.objects.get(pk=req.pk), ws)
        self.assertNotIn('activeDeadlineSeconds', job['spec'])
        self.assertNotIn('activeDeadlineSeconds', job['spec']['template']['spec'])
        self.assertEqual(job['spec']['backoffLimit'], 0)
        self.assertIsNone(ws.expire_at)

    def test_interactive_is_gated_and_nonroot(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        _, pod, _ = task_objects(ResourceRequest.objects.get(pk=req.pk), Workspace.objects.get(request=req))
        self.assertEqual(pod['spec']['schedulingGates'], [{'name': 'kueue.x-k8s.io/admission'},{'name':'gpu.platform/allocation'}])
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertTrue(pod['spec']['securityContext']['runAsNonRoot'])
        self.assertFalse(any('hostPath' in v for v in pod['spec']['volumes']))

    def test_private_image_and_key_required(self):
        self.image.owner = self.b
        self.image.shared = False
        self.image.save()
        req = task(self.a, self.image)
        with self.assertRaises(ValidationError): approve(req.pk, self.admin)
        self.image.shared = True
        self.image.save()
        self.a.sshkey_set.update(enabled=False)
        with self.assertRaises(ValidationError): approve(req.pk, self.admin)

    def test_batch_environment_cannot_override_runner(self):
        req = task(self.a, self.image, mode='batch', starts_at=None, ends_at=None, argv=['true'], environment={'LD_PRELOAD': 'bad'})
        with self.assertRaises(ValidationError): approve(req.pk, self.admin)

    def test_manual_stop_preserves_permanent_volume(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        request_stop(req.pk, self.a)
        self.assertTrue(Workspace.objects.get(request=req).stop_requested)
        self.assertIsNone(VolumeAllocation.objects.get(kind='home').retain_until)

    def test_extension_only_interactive_and_audited(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        extend(req.pk, self.admin, req.ends_at+timedelta(hours=2), 'Extra experiment')
        self.assertEqual(ResourceRequest.objects.get(pk=req.pk).ends_at, req.ends_at+timedelta(hours=2))
        self.assertEqual(AuditLog.objects.filter(action='extend').count(), 1)
        batch = task(self.b, self.image, mode='batch', starts_at=None, ends_at=None, argv=['true'])
        approve(batch.pk, self.admin)
        with self.assertRaises(ValidationError): extend(batch.pk, self.admin, timezone.now()+timedelta(hours=1), 'invalid')

    def test_reject_requires_reason(self):
        req = task(self.a, self.image)
        with self.assertRaises(ValidationError): reject(req.pk, self.admin, '')
        reject(req.pk, self.admin, 'Insufficient purpose')
        self.assertEqual(ResourceRequest.objects.get(pk=req.pk).status, 'rejected')

    def test_untrusted_ssh_key_options_rejected(self):
        key = self.a.sshkey_set.get().public_key
        with self.assertRaises(ValidationError): parse_ssh_key('command="evil" ' + key)
        self.assertTrue(parse_ssh_key(key)[1].startswith('SHA256:'))

    def test_cannot_stop_others(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        with self.assertRaises(ValidationError): request_stop(req.pk, self.b)

    def test_home_expansion_reserves_delta_and_preserves_identity(self):
        req = task(self.a, self.image)
        approve(req.pk, self.admin)
        home = VolumeAllocation.objects.get(kind='home')
        UserGroup.objects.update(home_gib=100)
        resize_home(self.a, 80, self.admin)
        home.refresh_from_db()
        self.assertEqual(home.size_gib, 80)
        self.assertEqual(StoragePool.objects.get().reserved_gib, 180)
        with self.assertRaises(ValidationError): resize_home(self.a, 40, self.admin)
        self.assertEqual(VolumeAllocation.objects.get(kind='home').pk, home.pk)

    def test_request_cannot_exceed_observed_machine_total(self):
        SystemObservation.objects.create(name='resources', data={'gpu': {'total': 1}})
        with self.assertRaises(ValidationError): approve(task(self.a, self.image, gpu=2).pk, self.admin)

    def test_system_volume_budget_is_reserved_on_its_pool(self):
        from portal.configuration import config
        pool = StoragePool.objects.get(); pool.storage_class=config()['platform']['data_storage_class'];pool.free_gib=400;pool.save()
        with self.assertRaises(ValidationError):approve(task(self.a,self.image).pk,self.admin)
        self.assertFalse(VolumeAllocation.objects.exists())

    def test_full_pool_uses_another_independent_pool(self):
        StoragePool.objects.update(free_gib=0)
        other=StoragePool.objects.create(name='available',path='/data-other',storage_class='other',capacity_gib=1000,free_gib=1000,healthy=True,observed_at=timezone.now())
        approve(task(self.a,self.image).pk,self.admin)
        self.assertEqual(set(VolumeAllocation.objects.values_list('pool_id',flat=True)),{other.pk})

class ConcurrentApprovalTests(TransactionTestCase):
    reset_sequences = True
    def setUp(self): self.admin, self.a, self.b, self.image = fixtures()

    def race(self, ids):
        barrier = Barrier(len(ids))
        def worker(pk):
            connections.close_all()
            barrier.wait(timeout=10)
            try:
                return approve(pk, self.admin).status
            except ValidationError:
                return 'capacity_rejected'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(ids)) as ex:
            return list(ex.map(worker, ids))

    def test_two_clicks_create_exactly_one_workspace(self):
        req = task(self.a, self.image)
        self.assertEqual(self.race([req.pk, req.pk]), ['approved', 'approved'])
        self.assertEqual(Workspace.objects.count(), 1)
        self.assertEqual(StoragePool.objects.get().reserved_gib, 150)

    def test_two_users_cannot_overreserve_pool(self):
        StoragePool.objects.update(capacity_gib=250, free_gib=250)
        a, b = task(self.a, self.image), task(self.b, self.image)
        self.assertCountEqual(self.race([a.pk, b.pk]), ['approved', 'capacity_rejected'])
        self.assertEqual(StoragePool.objects.get().reserved_gib, 150)

class ResourceObservationTests(TestCase):
    def test_no_gpu_pod_reservations_and_empty_cluster(self):
        self.assertEqual(resource_summary([], [])['gpu']['available'], 0)
        node = {'metadata': {'name': 'test'}, 'status': {'allocatable': {'cpu': '10', 'memory': '64Gi', 'gpu.platform/compute': '400', 'gpu.platform/memory-mib':'98304'}, 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        result = resource_summary([node], [])
        self.assertEqual(result['gpu']['available'], 4)
        self.assertEqual(result['memory_gib']['available'], 64)

    def test_allocatable_and_real_pod_reservations(self):
        node = {'metadata': {'name': 'test'}, 'status': {'allocatable': {'cpu': '10', 'memory': '64Gi', 'gpu.platform/compute': '400', 'gpu.platform/memory-mib':'98304'}, 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        pod = {'spec': {'nodeName': 'test', 'containers': [{'resources': {'requests': {'cpu': '1500m', 'memory': '2Gi', 'gpu.platform/compute': '100', 'gpu.platform/memory-mib':'8192'}}}]}, 'status': {'phase': 'Running'}}
        result = resource_summary([node], [pod])
        self.assertEqual(result['gpu']['available'], 3)
        self.assertEqual(result['cpu']['available'], 8.5)
        self.assertEqual(result['memory_gib']['available'], 62)
