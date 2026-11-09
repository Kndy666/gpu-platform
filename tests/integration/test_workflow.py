import json
from unittest.mock import patch
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from portal.forms import RequestForm
from portal.models import EnvironmentSnapshot, ImageArtifact, ResourceRequest, VolumeAllocation, Workspace, SystemObservation
from portal.services import approve, work_volume, request_stop
from portal.reconcile import Reconciler, mark_finished
from portal.snapshots import sync_snapshots
from portal.timeline import allocation_timeline
from tests.factories import fixtures, task
from tests.integration.test_reconcile import FakeKube

class WorkflowTests(TestCase):
    def setUp(self):
        self.admin,self.a,self.b,self.image=fixtures()
        self.kube=FakeKube()
        from portal.kube import KubeClient
        self.kube.path=KubeClient.path
        def call(method,path,body=None,**kwargs):
            if method=='DELETE': self.kube.objects.pop(('configmaps',path.rsplit('/',1)[-1]),None)
        self.kube.call=call

    def source(self):
        source=task(self.a,self.image,gpu=0)
        approve(source.pk,self.admin)
        source.status='running';source.save(update_fields=['status'])
        return source

    def test_future_environment_does_not_create_pod_or_allocate_port(self):
        req=task(self.a,self.image,starts_at=timezone.now()+timedelta(hours=1),ends_at=timezone.now()+timedelta(hours=2))
        approve(req.pk,self.admin);req.refresh_from_db()
        Reconciler(self.kube).reconcile_task(req)
        self.assertEqual(req.status,'scheduled')
        self.assertEqual(self.kube.created,[])
        self.assertIsNone(Workspace.objects.get(request=req).ssh_port)

    def test_approval_rejects_elapsed_window(self):
        req=task(self.a,self.image,starts_at=timezone.now()-timedelta(hours=2),ends_at=timezone.now()-timedelta(hours=1))
        with self.assertRaises(ValidationError):approve(req.pk,self.admin)

    def test_handoff_reuses_volume_and_releases_source_only_after_image_verified(self):
        source=self.source()
        target=task(self.a,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['bash','-lc','echo train'])
        approve(target.pk,self.admin);target.refresh_from_db()
        self.assertEqual(target.status,'preparing')
        self.assertEqual(VolumeAllocation.objects.count(),2)
        self.assertEqual(work_volume(target).pk,work_volume(source).pk)
        snap=EnvironmentSnapshot.objects.get(target=target)
        sync_snapshots(self.kube)
        self.assertFalse(Workspace.objects.get(request=source).stop_requested)
        obj=self.kube.objects['configmaps','snapshot-'+snap.pk.hex]
        obj['data'].update(status='succeeded',result=json.dumps({'reference':'registry/user-a/env:1','digest':'sha256:'+'b'*64,'size_bytes':1024}))
        sync_snapshots(self.kube)
        target.refresh_from_db()
        self.assertEqual(target.status,'approved')
        self.assertTrue(Workspace.objects.get(request=source).stop_requested)
        self.assertEqual(target.image.owner_id,self.a.pk)
        self.assertTrue(target.image.temporary)
        mark_finished(source,'canceled')
        self.assertIsNone(work_volume(target).retain_until)
        mark_finished(target,'succeeded',0)
        self.assertIsNotNone(work_volume(target).retain_until)

    def test_canceled_training_does_not_stop_source_when_save_finishes(self):
        source=self.source()
        target=task(self.a,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['true'])
        approve(target.pk,self.admin)
        snap=EnvironmentSnapshot.objects.get(target=target)
        sync_snapshots(self.kube)
        request_stop(target.pk,self.a)
        self.kube.objects['configmaps','snapshot-'+snap.pk.hex]['data'].update(status='succeeded',result=json.dumps({'reference':'registry/user-a/env:2','digest':'sha256:'+'c'*64,'size_bytes':100}))
        sync_snapshots(self.kube)
        self.assertFalse(Workspace.objects.get(request=source).stop_requested)
        target.refresh_from_db();self.assertEqual(target.status,'canceled')

    def test_cancel_scheduled_environment_releases_reservation_immediately(self):
        req=task(self.a,self.image,starts_at=timezone.now()+timedelta(hours=1),ends_at=timezone.now()+timedelta(hours=2))
        approve(req.pk,self.admin)
        request_stop(req.pk,self.a)
        req.refresh_from_db()
        Reconciler(self.kube).reconcile_task(req)
        req.refresh_from_db()
        self.assertEqual(req.status,'canceled')
        self.assertTrue(Workspace.objects.get(request=req).cleanup_complete)
        self.assertIsNotNone(work_volume(req).retain_until)
        self.assertEqual(self.kube.created,[])

    def test_cancel_pending_handoff_does_not_create_snapshot_operation(self):
        source=self.source()
        target=task(self.a,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['true'])
        approve(target.pk,self.admin)
        request_stop(target.pk,self.a)
        sync_snapshots(self.kube)
        target.refresh_from_db()
        self.assertEqual(target.status,'canceled')
        self.assertEqual(EnvironmentSnapshot.objects.get(target=target).status,'failed')
        self.assertFalse(Workspace.objects.get(request=source).stop_requested)
        self.assertFalse(self.kube.created)

    def test_cancel_while_waiting_for_source_cleanup_completes(self):
        source=self.source()
        target=task(self.a,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['true'])
        approve(target.pk,self.admin)
        target.status='approved';target.save(update_fields=['status'])
        request_stop(target.pk,self.a)
        Reconciler(self.kube).reconcile_task(target)
        target.refresh_from_db()
        self.assertEqual(target.status,'canceled')
        self.assertTrue(Workspace.objects.get(request=target).cleanup_complete)
        self.assertFalse(self.kube.created)

    def test_failed_snapshot_removes_operation_configmap(self):
        source=self.source()
        snapshot=EnvironmentSnapshot.objects.create(source=source)
        sync_snapshots(self.kube)
        key=('configmaps','snapshot-'+snapshot.pk.hex)
        self.kube.objects[key]['data'].update(status='failed',error='save failed')
        sync_snapshots(self.kube)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status,'failed')
        self.assertNotIn(key,self.kube.objects)

    def test_terminal_snapshot_cleanup_retries_after_delete_failure(self):
        source=self.source()
        snapshot=EnvironmentSnapshot.objects.create(source=source)
        sync_snapshots(self.kube)
        key=('configmaps','snapshot-'+snapshot.pk.hex)
        self.kube.objects[key]['data'].update(status='failed',error='save failed')
        with patch.object(self.kube,'call',side_effect=RuntimeError('API unavailable')):
            with self.assertRaises(RuntimeError):
                sync_snapshots(self.kube)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status,'failed')
        self.assertIn(key,self.kube.objects)
        sync_snapshots(self.kube)
        self.assertNotIn(key,self.kube.objects)

    def test_cancel_does_not_lose_result_if_worker_claims_pending_operation(self):
        from portal.kube import KubeError
        source=self.source()
        target=task(self.a,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['true'])
        approve(target.pk,self.admin)
        sync_snapshots(self.kube)
        snapshot=EnvironmentSnapshot.objects.get(target=target)
        key=('configmaps','snapshot-'+snapshot.pk.hex)
        obj=self.kube.objects[key]
        obj['metadata']['resourceVersion']='1'
        request_stop(target.pk,self.a)
        def worker_claimed(method,path,body=None,**kwargs):
            self.assertEqual(body['preconditions']['resourceVersion'],'1')
            obj['data']['status']='working'
            obj['metadata']['resourceVersion']='2'
            raise KubeError(409,'resourceVersion changed')
        with patch.object(self.kube,'call',side_effect=worker_claimed):
            with self.assertRaises(KubeError):sync_snapshots(self.kube)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status,'pending')
        obj['data'].update(status='succeeded',result=json.dumps({
            'reference':'registry/user-a/env:race','digest':'sha256:'+'d'*64,'size_bytes':100}))
        sync_snapshots(self.kube)
        snapshot.refresh_from_db();target.refresh_from_db()
        self.assertEqual(snapshot.status,'succeeded')
        self.assertTrue(snapshot.artifact.temporary)
        self.assertEqual(target.status,'canceled')
        self.assertFalse(Workspace.objects.get(request=source).stop_requested)
        self.assertNotIn(key,self.kube.objects)

    def test_oversized_snapshot_deletes_only_new_tag(self):
        import base64
        from portal.configuration import config
        source=self.source()
        snapshot=EnvironmentSnapshot.objects.create(source=source)
        sync_snapshots(self.kube)
        key=('configmaps','snapshot-'+snapshot.pk.hex)
        reference=config()['registry']['host']+f'/user-{self.a.pk}/environment:new-version'
        self.kube.objects[key]['data'].update(status='succeeded',result=json.dumps({
            'reference':reference,'digest':'sha256:'+'a'*64,'size_bytes':2**60}))
        self.kube.objects['secrets','registry-state']={'data':{'users.json':base64.b64encode(json.dumps({str(self.a.pk):{'login':'test','password':'test'}}).encode()).decode()}}
        with patch('portal.registry.RegistryClient') as client:
            sync_snapshots(self.kube)
            client.return_value.delete_tag.assert_called_once_with(f'user-{self.a.pk}/environment','new-version')
            client.return_value.delete_manifest.assert_not_called()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status,'failed')
        self.assertNotIn(key,self.kube.objects)

    def test_cannot_handoff_another_users_environment(self):
        source=self.source()
        req=task(self.b,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['true'])
        with self.assertRaises(ValidationError):approve(req.pk,self.admin)

    def test_shared_work_volume_is_not_collected_during_training(self):
        source=self.source()
        target=task(self.a,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['true'])
        approve(target.pk,self.admin)
        volume=work_volume(source);volume.retain_until=timezone.now()-timedelta(days=2);volume.notified_at=timezone.now()-timedelta(days=1);volume.save()
        Reconciler(self.kube).collect()
        volume.refresh_from_db();self.assertNotEqual(volume.state,'deleted')

    def test_training_dispatch_waits_until_source_container_is_released(self):
        source=self.source()
        target=task(self.a,self.image,mode='batch',starts_at=None,ends_at=None,source_environment=source,argv=['true'])
        approve(target.pk,self.admin)
        target.status='approved';target.save(update_fields=['status'])
        Reconciler(self.kube).reconcile_task(target)
        self.assertFalse(self.kube.created)
        Workspace.objects.filter(request=source).update(cleanup_complete=True)
        Reconciler(self.kube).reconcile_task(target)
        self.assertTrue(any(kind=='jobs' for kind,name in self.kube.created))

    def test_regular_command_form_accepts_shell_and_variables(self):
        form=RequestForm({'source_kind':'image','image':str(self.image.pk),'purpose':'run','gpu':1,'gpu_requests':[{'compute_percent':100,'memory_gib':8}],'cpu':2,'memory_gib':4,'workspace_gib':20,'command':'python train.py --epochs 10','working_dir':'/workspace','variables':'OMP_NUM_THREADS=2'},user=self.a,mode='batch')
        self.assertTrue(form.is_valid(),form.errors)
        req=form.save()
        self.assertEqual(req.argv,['/bin/bash','-c','python train.py --epochs 10'])
        self.assertEqual(req.environment,{'OMP_NUM_THREADS':'2'})
        self.assertIsNone(req.ends_at)
        import os,subprocess
        output=subprocess.check_output(['/bin/bash', req.argv[1], 'printf %s \"$PATH\"'],env={**os.environ,'PATH':'/project-venv/bin:'+os.environ['PATH']},text=True)
        self.assertTrue(output.startswith('/project-venv/bin:'))

    def test_date_form_uses_beijing_time(self):
        today=timezone.localdate()+timedelta(days=1)
        form=RequestForm({'image':str(self.image.pk),'purpose':'env','gpu':0,'cpu':2,'memory_gib':4,'workspace_gib':20,'starts_at':str(today)+'T10:00','ends_at':str(today)+'T12:00','working_dir':'/workspace'},user=self.a,mode='interactive')
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['starts_at'].utcoffset(),timedelta(hours=8))

    def test_timeline_shows_gpu_uuid_and_hides_other_users_title(self):
        source=self.source();source.gpu=1;source.gpu_requests=[{'compute_percent':100,'memory_gib':8}];source.save()
        ws=Workspace.objects.get(request=source);ws.started_at=timezone.now()-timedelta(minutes=5);ws.gpu_devices=['GPU-test']
        ws.gpu_allocations=[{'slot':0,'uuid':'GPU-test','index':0,'sm_count':114}];ws.save()
        SystemObservation.objects.create(name='gpu_inventory',data={'devices':[{'uuid':'GPU-test','index':0,'name':'RTX 4090 D'}]})
        own=allocation_timeline(self.a)
        self.assertTrue(any(b['row']=='GPU-test' and b['title']==source.purpose for b in own['blocks']))
        other=allocation_timeline(self.b)
        self.assertTrue(all(b['url'] is None and b['title']=='已分配' for b in other['blocks']))

    def test_management_requires_staff_and_renders_for_admin(self):
        self.client.force_login(self.a);self.assertEqual(self.client.get('/management/').status_code,302)
        self.client.force_login(self.admin);self.assertEqual(self.client.get('/management/').status_code,200)
