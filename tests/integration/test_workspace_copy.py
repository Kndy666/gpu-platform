import json
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from portal.models import ResourceRequest,VolumeAllocation,Workspace,EnvironmentSnapshot,StoragePool
from portal.services import approve,work_volume,work_owner,request_stop
from portal.reconcile import Reconciler,mark_finished
from portal.volumes import volume_busy
from portal.workspace_copy import ensure_workspace_copy,copy_name,copy_job,COPY_SCRIPT
from tests.factories import fixtures,task
from tests.integration.test_reconcile import FakeKube


class WorkspaceCopyTests(TestCase):
    @classmethod
    def setUpTestData(cls):cls.admin,cls.user,cls.other,cls.image=fixtures()

    def source(self):
        source=task(self.user,self.image,gpu=1,workspace_gib=100)
        approve(source.pk,self.admin)
        ResourceRequest.objects.filter(pk=source.pk).update(status='running')
        VolumeAllocation.objects.filter(request=source).update(state='bound')
        source.refresh_from_db();return source

    def target(self,source,size):
        target=task(self.user,self.image,mode='batch',ends_at=None,starts_at=None,source_environment=source,
            workspace_gib=size,argv=['true'])
        approve(target.pk,self.admin);target.refresh_from_db();return target

    def prepare_copy(self,size):
        source=self.source();target=self.target(source,size)
        snapshot=EnvironmentSnapshot.objects.get(target=target);snapshot.status='succeeded';snapshot.save()
        target.status='approved';target.save(update_fields=['status'])
        mark_finished(source,'canceled')
        Workspace.objects.filter(request=source).update(cleanup_complete=True)
        source.refresh_from_db();target.refresh_from_db()
        kube=FakeKube();kube.path=lambda *args: '/'.join(str(v) for v in args if v is not None)
        kube.objects['persistentvolumeclaims',work_volume(target).pvc_name]={'metadata':{'name':work_volume(target).pvc_name},'status':{'phase':'Pending'}}
        return source,target,kube

    def test_same_size_reuses_source_claim_and_gpu_source_is_allowed(self):
        source=self.source();target=self.target(source,100)
        self.assertEqual(target.status,'preparing')
        self.assertEqual(work_owner(target).pk,source.pk)
        self.assertEqual(work_volume(source).pk,work_volume(target).pk)
        self.assertEqual(VolumeAllocation.objects.count(),2)

    def test_growing_and_shrinking_reserve_separate_claim_and_preserve_source(self):
        for size in (20,180):
            with self.subTest(size=size):
                source=self.source();target=self.target(source,size)
                destination=work_volume(target)
                self.assertEqual(destination.request_id,target.pk)
                self.assertEqual((destination.state,destination.size_gib),('copy_pending',size))
                self.assertEqual(work_volume(source).size_gib,100)
                self.assertTrue(volume_busy(work_volume(source)))

    def test_first_consumer_job_is_created_before_destination_binds(self):
        source,target,kube=self.prepare_copy(20)
        self.assertFalse(ensure_workspace_copy(kube,target))
        job=kube.objects['jobs',copy_name(target)]
        self.assertEqual(job['spec']['backoffLimit'],0)
        pod=job['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertTrue(pod['containers'][0]['volumeMounts'][0]['readOnly'])
        self.assertEqual(pod['volumes'][0]['persistentVolumeClaim']['claimName'],work_volume(source).pvc_name)
        self.assertNotIn('kueue.x-k8s.io/queue-name',job['metadata']['labels'])
        self.assertEqual(work_volume(target).state,'copying')

    def test_success_releases_old_claim_after_verified_copy(self):
        source,target,kube=self.prepare_copy(140)
        ensure_workspace_copy(kube,target)
        kube.objects['jobs',copy_name(target)]['status']={'succeeded':1}
        self.assertTrue(ensure_workspace_copy(kube,target))
        self.assertEqual(work_volume(target).state,'bound')
        self.assertEqual(work_volume(source).state,'deleting')
        self.assertFalse(volume_busy(work_volume(source)))

    def test_failed_copy_keeps_original_data_and_schedules_partial_volume_cleanup(self):
        source,target,kube=self.prepare_copy(20)
        ensure_workspace_copy(kube,target)
        kube.objects['jobs',copy_name(target)]['status']={'failed':1}
        self.assertFalse(ensure_workspace_copy(kube,target))
        target.refresh_from_db()
        self.assertEqual(target.status,'failed')
        self.assertEqual(work_volume(source).state,'bound')
        self.assertIsNotNone(work_volume(source).retain_until)
        self.assertIsNotNone(work_volume(target).retain_until)

    def test_copy_never_starts_while_source_container_exists(self):
        source=self.source();target=self.target(source,20)
        target.status='approved';target.save(update_fields=['status'])
        kube=FakeKube();Reconciler(kube).reconcile_task(target)
        self.assertFalse(kube.created)

    def test_quota_counts_replacement_size_and_pool_still_reserves_temporary_space(self):
        group=self.user.profile.group;group.workspace_gib=120;group.save(update_fields=['workspace_gib'])
        source=self.source();pool=StoragePool.objects.get();before=pool.reserved_gib
        target=self.target(source,120)
        pool.refresh_from_db();self.assertEqual(pool.reserved_gib,before+120)
        self.assertEqual(work_volume(target).size_gib,120)

    def test_cancel_removes_copy_job_and_retains_original_volume(self):
        source,target,kube=self.prepare_copy(20)
        ensure_workspace_copy(kube,target)
        request_stop(target.pk,self.user);target.refresh_from_db()
        Reconciler(kube).reconcile_task(target)
        self.assertNotIn(('jobs',copy_name(target)),kube.objects)
        self.assertEqual(work_volume(source).state,'bound')
        self.assertIsNotNone(work_volume(source).retain_until)
