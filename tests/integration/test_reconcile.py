from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from portal.models import ResourceRequest, VolumeAllocation, Workspace
from portal.reconcile import Reconciler
from portal.services import approve, request_stop
from tests.factories import fixtures, task

class FakeKube:
    def __init__(self): self.objects = {}; self.created = []
    def get(self, resource, ns, name, group='v1'): return deepcopy(self.objects.get((resource, name)))
    def ensure(self, obj, resource, ns, group='v1'):
        key = resource, obj['metadata']['name']
        if key not in self.objects:
            obj = deepcopy(obj)
            obj['metadata']['uid'] = 'fake-' + str(len(self.created))
            self.objects[key] = obj
            self.created.append(key)
        return self.get(resource, ns, key[1])
    def list(self, resource, ns=None, selector=None, group='v1'):
        values = [deepcopy(v) for (r, n), v in self.objects.items() if r == resource]
        if selector:
            k, value = selector.split('=')
            values = [v for v in values if v['metadata'].get('labels', {}).get(k) == value]
        return values
    def delete_owned(self, resource, ns, name, owner, group='v1'):
        obj = self.objects.get((resource, name))
        if obj:
            assert obj['metadata']['labels']['platform.task'] == str(owner)
            del self.objects[resource, name]
            return False
        return True

class ReconcileTests(TestCase):
    def setUp(self):
        self.admin, self.a, self.b, self.image = fixtures()
        self.kube = FakeKube()
        self.rec = Reconciler(self.kube)

    def prepare(self, mode='batch'):
        req = task(self.a, self.image, mode=mode, **({'starts_at': None, 'ends_at': None} if mode == 'batch' else {}), argv=['sleep', '10'] if mode == 'batch' else [])
        approve(req.pk, self.admin)
        req.refresh_from_db()
        self.rec.reconcile_task(req)
        req.refresh_from_db()
        return req, Workspace.objects.get(request=req)

    def pod(self, req, phase='Running', code=None):
        ws = Workspace.objects.get(request=req)
        pod = {'metadata': {'name': ws.object_name + ('-pod' if req.mode == 'batch' else ''),
                           'uid': 'test-pod', 'labels': {'platform.task': str(req.pk)}},
               'spec': {'containers': [{'resources': {'limits': {'nvidia.com/gpu': '1'}}}]}, 'status': {'phase': phase, 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        if code is not None:
            pod['status']['containerStatuses'] = [{'name': 'workspace', 'state': {'terminated': {'exitCode': code}}}]
        self.kube.objects['pods', pod['metadata']['name']] = pod

    def test_repeated_ticks_do_not_duplicate_jobs_or_volumes(self):
        req, ws = self.prepare()
        self.rec.reconcile_task(req)
        self.assertEqual(len([r for r in self.kube.created if r[0] == 'jobs']), 1)
        self.assertEqual(len([r for r in self.kube.created if r[0] == 'persistentvolumeclaims']), 2)

    def test_exit_42_records_failure_before_cleanup_without_retry(self):
        req, ws = self.prepare()
        self.pod(req, 'Failed', 42)
        self.rec.reconcile_task(req)
        req.refresh_from_db(); ws.refresh_from_db()
        self.assertEqual((req.status, ws.exit_code), ('failed', 42))
        self.assertIsNotNone(VolumeAllocation.objects.get(request=req).retain_until)
        self.assertIsNone(VolumeAllocation.objects.get(kind='home').retain_until)
        self.rec.reconcile_task(req)
        self.assertEqual(len([r for r in self.kube.created if r[0] == 'jobs']), 1)

    def test_short_task_records_exact_runtime_before_first_running_poll(self):
        req, ws = self.prepare()
        self.pod(req, 'Succeeded', 0)
        started = timezone.now() - timedelta(seconds=3)
        finished = started + timedelta(seconds=1)
        pod = self.kube.objects['pods', ws.object_name+'-pod']
        pod['status']['containerStatuses'][0]['state']['terminated'].update(startedAt=started.isoformat(), finishedAt=finished.isoformat())
        pod['metadata']['annotations'] = {'platform.gpu-devices': '["GPU-example"]'}
        self.rec.reconcile_task(req)
        ws.refresh_from_db()
        self.assertEqual(ws.started_at, started)
        self.assertEqual(ws.ended_at, finished)
        self.assertEqual(ws.gpu_devices, ['GPU-example'])

    def test_batch_runs_past_interactive_limit(self):
        req, ws = self.prepare()
        ws.started_at = timezone.now() - timedelta(days=100)
        ws.save()
        self.pod(req)
        self.rec.reconcile_task(req)
        req.refresh_from_db(); ws.refresh_from_db()
        self.assertEqual(req.status, 'running')
        self.assertIsNone(ws.expire_at)

    def test_success_closes_service_and_releases_port(self):
        req, ws = self.prepare()
        self.pod(req, 'Succeeded', 0)
        self.rec.reconcile_task(req)
        req.refresh_from_db()
        self.rec.reconcile_task(req)
        ws.refresh_from_db()
        self.assertEqual(req.status, 'succeeded')
        self.assertIsNone(ws.ssh_port)
        self.assertTrue(ws.cleanup_complete)

    def test_cancellation_distinct_from_failure(self):
        req, ws = self.prepare()
        request_stop(req.pk, self.admin)
        self.rec.reconcile_task(req)
        req.refresh_from_db()
        self.assertEqual(req.status, 'canceled')

    def test_interactive_expiry_does_not_delete_home(self):
        req, ws = self.prepare('interactive')
        ws.expire_at = timezone.now() - timedelta(seconds=1)
        ws.save()
        self.rec.reconcile_task(req)
        req.refresh_from_db()
        self.assertEqual(req.status, 'expired')
        self.assertTrue(VolumeAllocation.objects.filter(kind='home').exists())

    def test_missing_dispatched_job_never_recreated(self):
        req, ws = self.prepare()
        del self.kube.objects['jobs', ws.object_name]
        self.rec.reconcile_task(req)
        req.refresh_from_db()
        self.assertEqual(req.status, 'failed')
        self.assertEqual(len([r for r in self.kube.created if r[0] == 'jobs']), 1)

    def test_missing_bound_home_is_not_replaced_with_empty_volume(self):
        req, ws = self.prepare()
        home = VolumeAllocation.objects.get(kind='home')
        home.state = 'bound'; home.save(update_fields=['state'])
        del self.kube.objects['persistentvolumeclaims', home.pvc_name]
        with self.assertRaises(RuntimeError):
            self.rec.reconcile_task(req)
        self.assertNotIn(('persistentvolumeclaims', home.pvc_name), self.kube.objects)

    def test_ssh_port_skips_existing_external_service(self):
        self.kube.objects['services', 'external'] = {'metadata': {'name': 'external'}, 'spec': {'ports': [{'nodePort': 30022}]}}
        req, ws = self.prepare()
        self.assertEqual(ws.ssh_port, 30023)

    def test_running_process_waits_for_ssh_readiness(self):
        req, ws = self.prepare()
        self.pod(req)
        self.kube.objects['pods', ws.object_name+'-pod']['status']['conditions'][0]['status'] = 'False'
        self.rec.reconcile_task(req)
        req.refresh_from_db()
        self.assertEqual(req.status, 'starting')

    def test_tick_skips_released_history_but_retries_pending_cleanup(self):
        released = task(self.a, self.image, status='succeeded')
        Workspace.objects.create(request=released, object_name='released', cleanup_complete=True)
        pending = task(self.a, self.image, status='succeeded')
        Workspace.objects.create(request=pending, object_name='pending', cleanup_complete=False)
        with patch('portal.builds.BuildReconciler.tick'), patch('portal.snapshots.sync_snapshots'), \
                patch('portal.queues.sync_user_queues'), patch('portal.image_lifecycle.collect_images'), \
                patch.object(self.rec, 'observe'), patch.object(self.rec, 'collect'), \
                patch.object(self.rec, 'reconcile_task') as reconcile:
            self.rec.tick()
        self.assertEqual(reconcile.call_count, 1)
        req, ws = reconcile.call_args.args
        self.assertEqual(req.pk, pending.pk)
        self.assertEqual(ws.request_id, pending.pk)
