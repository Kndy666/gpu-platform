import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from portal.builds import BuildReconciler
from portal.management_forms import GroupForm, MemberForm
from portal.models import EnvironmentSnapshot, ImageArtifact, ImageBuild, StoragePool, SystemObservation, UserGroup, VolumeAllocation, GROUP_QUOTA_FIELDS
from portal.api_auth import issue_token
from portal.queues import queue_objects
from portal.services import approve, policy_for, resize_home
from portal.snapshots import sync_snapshots
from tests.factories import fixtures, task, member_form_data


class UnlimitedGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.user, cls.other, cls.image = fixtures()
        cls.group = UserGroup.objects.create(name='管理员', unlimited=True, gpu_compute_percent=0, cpu=1, memory_gib=1,
            home_gib=1, workspace_gib=1, image_gib=1, concurrent_tasks=1, interactive_hours=1)
        cls.user.profile.group = cls.group
        from portal.policies import settings_from_group
        for name,value in settings_from_group(cls.group).items():setattr(cls.user.profile,name,value)
        cls.user.profile.save()

    def test_compute_storage_and_duration_exceed_old_limits(self):
        for i in range(2):
            req = task(self.user, self.image, gpu=2, cpu=8, memory_gib=32, workspace_gib=100,
                       ends_at=timezone.now()+timedelta(days=30))
            approve(req.pk, self.admin)
        self.assertEqual(VolumeAllocation.objects.filter(kind='work').count(), 2)
        resize_home(self.user, 200, self.admin)
        self.assertEqual(VolumeAllocation.objects.get(kind='home').size_gib, 200)

    def test_unlimited_group_has_no_stored_quotas_even_when_values_are_supplied(self):
        self.group.refresh_from_db()
        self.assertTrue(all(getattr(self.group,name) is None for name in GROUP_QUOTA_FIELDS))
        limited=self.other.profile.group
        previous={name:getattr(limited,name) for name in GROUP_QUOTA_FIELDS}
        limited.unlimited=True
        limited.save(update_fields=['unlimited'])
        limited.refresh_from_db()
        self.assertTrue(all(getattr(limited,name) is None for name in GROUP_QUOTA_FIELDS))
        with self.assertRaises(ValidationError):
            limited.unlimited=False
            limited.save()
        for name,value in previous.items():setattr(limited,name,value)
        limited.save()
        limited.refresh_from_db()
        self.assertEqual({name:getattr(limited,name) for name in GROUP_QUOTA_FIELDS},previous)

    def test_database_rejects_quotas_on_unlimited_group_and_missing_limited_quotas(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            UserGroup.objects.filter(pk=self.group.pk).update(cpu=32)
        with self.assertRaises(IntegrityError),transaction.atomic():
            UserGroup.objects.filter(pk=self.other.profile.group_id).update(cpu=None)

    def test_api_omits_unlimited_quota_fields_and_clears_submitted_values(self):
        self.client.defaults['HTTP_AUTHORIZATION']='Bearer '+issue_token(self.admin)['token']
        response=self.client.get(f'/api/v1/admin/groups/{self.group.pk}')
        self.assertEqual(response.status_code,200)
        self.assertFalse(set(GROUP_QUOTA_FIELDS)&response.json()['data'].keys())
        response=self.client.patch(f'/api/v1/admin/groups/{self.group.pk}',
            data=json.dumps({'cpu':999,'home_gib':999}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.group.refresh_from_db()
        self.assertTrue(all(getattr(self.group,name) is None for name in GROUP_QUOTA_FIELDS))
        self.assertEqual(self.client.patch(f'/api/v1/admin/groups/{self.group.pk}',
            data=json.dumps({'unlimited':False}),content_type='application/json').status_code,400)

    def test_unlimited_default_group_can_create_members_with_real_volume_allocations(self):
        UserGroup.objects.filter(is_default=True).update(is_default=False)
        self.group.is_default=True;self.group.save(update_fields=['is_default'])
        user=get_user_model().objects.create_user('new-unlimited-member')
        self.assertEqual(user.profile.group_id,self.group.pk)
        self.assertEqual(user.profile.home_quota_gib,50)

    def test_unlimited_still_requires_available_disk_and_physical_resources(self):
        SystemObservation.objects.create(name='resources', data={'gpu': {'total': 4}})
        with self.assertRaisesMessage(ValidationError, '超出服务器可分配总量'):
            approve(task(self.user, self.image, gpu=5).pk, self.admin)
        StoragePool.objects.update(free_gib=0)
        with self.assertRaisesMessage(ValidationError, '没有单块磁盘'):
            approve(task(self.user, self.image).pk, self.admin)
        self.assertFalse(VolumeAllocation.objects.exists())

    def test_queue_has_no_borrowing_cap_or_concurrency_slots(self):
        queue, _ = queue_objects(self.user, policy_for(self.user))
        group = queue['spec']['resourceGroups'][0]
        self.assertNotIn('pods', group['coveredResources'])
        self.assertTrue(all('borrowingLimit' not in item for item in group['flavors'][0]['resources']))
        regular, _ = queue_objects(self.other, policy_for(self.other))
        self.assertIn('pods', regular['spec']['resourceGroups'][0]['coveredResources'])

    def test_unlimited_group_form_and_member_allocation(self):
        form = GroupForm({'name': '管理员', 'unlimited': 'on', 'approval_mode':'manual', 'extension_limit':1}, instance=self.group)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save().unlimited)
        form = MemberForm(member_form_data(self.user,home_quota_gib=200), actor=self.admin, member=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.client.force_login(self.admin)
        response = self.client.get('/management/groups/')
        self.assertContains(response, '资源不限额')
        self.assertContains(response, '∞')

    def test_switching_back_to_limited_checks_member_home_sizes(self):
        data = {field: getattr(self.group, field) for field in GroupForm.Meta.fields}
        data['unlimited'] = False
        form = GroupForm(data, instance=self.group)
        self.assertFalse(form.is_valid())
        self.assertIn('home_gib', form.errors)

    def test_build_accepts_images_above_old_quota(self):
        build = ImageBuild.objects.create(user=self.user, name='large-image', dockerfile='FROM test')
        ImageArtifact.objects.create(owner=self.user, name='existing', reference='test:existing', size_bytes=2**31)
        kube = Mock()
        kube.get.return_value = {'status': {'conditions': [{'type': 'Complete', 'status': 'True'}]}}
        kube.list.return_value = []
        with patch('portal.builds.RegistryClient') as client:
            client.return_value.manifest.return_value = ('sha256:'+'b'*64, 2**31)
            BuildReconciler(kube).one(build, {str(self.user.pk): {'login': 'test', 'password': 'test'}})
            client.return_value.delete_tag.assert_not_called()
        build.refresh_from_db()
        self.assertEqual(build.status, 'succeeded')
        self.assertEqual(build.artifact.size_bytes, 2**31)

    def test_saved_environment_accepts_image_above_old_quota(self):
        source = task(self.user, self.image, gpu=0, status='running')
        snapshot = EnvironmentSnapshot.objects.create(source=source)
        kube = Mock()
        kube.list.return_value = []
        kube.ensure.return_value = {'metadata': {'name': 'snapshot-'+snapshot.pk.hex}, 'data': {
            'status': 'succeeded', 'result': json.dumps({'reference': 'registry.test/user-7/environment:version',
                                                       'digest': 'sha256:'+'c'*64, 'size_bytes': 2**31})}}
        sync_snapshots(kube)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, 'succeeded')
        self.assertEqual(snapshot.artifact.size_bytes, 2**31)
