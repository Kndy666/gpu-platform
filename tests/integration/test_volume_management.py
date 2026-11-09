from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from portal.kube import KubeClient
from portal.models import EnvironmentSnapshot, PlatformSettings, StoragePool, VolumeAllocation, Workspace
from portal.reconcile import Reconciler, mark_finished
from portal.services import approve
from portal.volumes import collect_volume_deletions, request_volume_deletion
from tests.factories import fixtures, task


class VolumeKube:
    path = staticmethod(KubeClient.path)

    def __init__(self):
        self.objects, self.calls = {}, []

    def get(self, resource, namespace, name):
        return deepcopy(self.objects.get((resource, name)))

    def list(self, resource, namespace):
        return [deepcopy(obj) for (kind, _), obj in self.objects.items() if kind == resource]

    def call(self, method, path, body):
        self.calls.append((method, path, body))
        resource, name = path.split('/')[-2:]
        self.objects.pop((resource, name), None)


class VolumeManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.user, cls.other, cls.image = fixtures()

    def setUp(self):
        self.kube = VolumeKube()
        self.req = task(self.user, self.image)
        approve(self.req.pk, self.admin)
        self.work = VolumeAllocation.objects.get(request=self.req)
        self.home = VolumeAllocation.objects.get(user=self.user, kind='home')

    def finish(self):
        mark_finished(self.req, 'canceled')
        Workspace.objects.filter(request=self.req).update(cleanup_complete=True)

    def test_default_and_configured_retention_apply_to_newly_finished_tasks(self):
        self.assertEqual(PlatformSettings.load().workspace_retention_days, 3)
        now = timezone.now()
        mark_finished(self.req, 'succeeded', ended_at=now)
        self.work.refresh_from_db()
        self.assertEqual(self.work.retain_until, now + timedelta(days=3))
        settings = PlatformSettings.load()
        settings.workspace_retention_days = 21
        settings.save()
        other = task(self.other, self.image)
        approve(other.pk, self.admin)
        mark_finished(other, 'succeeded', ended_at=now)
        self.assertEqual(VolumeAllocation.objects.get(request=other).retain_until, now + timedelta(days=21))
        self.work.refresh_from_db()
        self.assertEqual(self.work.retain_until, now + timedelta(days=3))

    def test_active_task_and_unreleased_resources_block_both_volumes(self):
        for volume in (self.work, self.home):
            with self.assertRaises(ValidationError):
                request_volume_deletion(volume.pk, self.admin)
        mark_finished(self.req, 'canceled')
        with self.assertRaises(ValidationError):
            request_volume_deletion(self.work.pk, self.admin)

    def test_deletion_notice_falls_within_retention_instead_of_extending_it(self):
        self.finish()
        deadline = timezone.now() + timedelta(hours=12)
        self.work.retain_until = deadline
        self.work.save()
        Reconciler(self.kube).collect()
        self.work.refresh_from_db()
        self.assertIsNotNone(self.work.notified_at)
        self.assertNotEqual(self.work.state, 'deleted')
        with patch('django.utils.timezone.now', return_value=deadline + timedelta(seconds=1)):
            Reconciler(self.kube).collect()
        self.work.refresh_from_db()
        self.assertEqual(self.work.state, 'deleted')

    def test_environment_save_blocks_deletion(self):
        self.finish()
        EnvironmentSnapshot.objects.create(source=self.req, status='working')
        with self.assertRaises(ValidationError):
            request_volume_deletion(self.work.pk, self.admin)

    def test_pvc_and_pv_disappear_before_capacity_is_released(self):
        self.finish()
        self.kube.objects['persistentvolumeclaims', self.work.pvc_name] = {
            'metadata': {'uid': 'claim-uid', 'labels': {'platform.volume': str(self.work.pk), 'platform.user': str(self.user.pk)}},
            'spec': {'volumeName': 'disk-pv'}}
        self.kube.objects['persistentvolumes', 'disk-pv'] = {'metadata': {'name': 'disk-pv'}}
        request_volume_deletion(self.work.pk, self.admin)
        collect_volume_deletions(self.kube)
        self.assertEqual(self.kube.calls[0][2], {'preconditions': {'uid': 'claim-uid'}})
        collect_volume_deletions(self.kube)
        self.work.refresh_from_db()
        self.assertEqual(self.work.state, 'deleting')
        self.assertEqual(StoragePool.objects.get().reserved_gib, 150)
        del self.kube.objects['persistentvolumes', 'disk-pv']
        collect_volume_deletions(self.kube)
        collect_volume_deletions(self.kube)
        self.work.refresh_from_db()
        self.assertEqual(self.work.state, 'deleted')
        self.assertEqual(StoragePool.objects.get().reserved_gib, 50)

    def test_wrong_pvc_owner_is_preserved_and_error_is_visible(self):
        self.finish()
        request_volume_deletion(self.work.pk, self.admin)
        self.kube.objects['persistentvolumeclaims', self.work.pvc_name] = {'metadata': {'uid': 'foreign', 'labels': {}}}
        collect_volume_deletions(self.kube)
        self.work.refresh_from_db()
        self.assertIn('所有权不符', self.work.deletion_error)
        self.assertFalse(self.kube.calls)

    def test_untracked_pod_mount_prevents_deletion(self):
        self.finish()
        request_volume_deletion(self.work.pk, self.admin)
        self.kube.objects['pods', 'manual'] = {'spec': {'volumes': [{'persistentVolumeClaim': {'claimName': self.work.pvc_name}}]}}
        collect_volume_deletions(self.kube)
        self.work.refresh_from_db()
        self.assertEqual(self.work.state, 'deleting')
        self.assertIn('等待使用此卷', self.work.deletion_error)

    def test_home_deletion_blocks_approval_then_creates_fresh_volume(self):
        self.finish()
        request_volume_deletion(self.home.pk, self.admin)
        new = task(self.user, self.image)
        with self.assertRaisesMessage(ValidationError, '个人卷正在删除'):
            approve(new.pk, self.admin)
        # A pending request must be withdrawn before removing its personal volume.
        new.status = 'canceled'
        new.save()
        collect_volume_deletions(self.kube)
        self.home.refresh_from_db()
        self.assertEqual(self.home.state, 'deleted')
        new.status = 'submitted'
        new.save()
        approve(new.pk, self.admin)
        fresh = VolumeAllocation.objects.filter(user=self.user, kind='home').exclude(state='deleted').get()
        self.assertNotEqual(fresh.pk, self.home.pk)
        self.assertNotEqual(fresh.pvc_name, self.home.pvc_name)

    def test_delete_requires_staff_and_get_is_a_confirmation(self):
        self.finish()
        url = f'/management/storage/{self.work.pk}/delete/'
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), '永久删除数据卷')
        self.work.refresh_from_db()
        self.assertNotEqual(self.work.state, 'deleting')
        self.assertEqual(self.client.post(url).status_code, 302)
        self.work.refresh_from_db()
        self.assertEqual(self.work.state, 'deleting')
