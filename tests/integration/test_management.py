from django.contrib.auth import get_user_model
from django.test import TestCase

from portal.models import ImageArtifact, PlatformSettings, UserGroup, VolumeAllocation
from portal.services import approve
from tests.factories import fixtures, task, member_form_data


class ManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.a, cls.b, cls.image = fixtures()
        cls.default_group = UserGroup.objects.get(name='临时用户')
        cls.longterm = UserGroup.objects.create(name='长期用户', home_gib=100)

    def setUp(self):
        self.client.force_login(self.admin)

    def member_data(self, **overrides):
        data = member_form_data(self.a)
        if 'group' in overrides:
            group = UserGroup.objects.get(pk=overrides['group'])
            from portal.models import GROUP_QUOTA_FIELDS
            data.update({name:getattr(group,name) for name in GROUP_QUOTA_FIELDS})
            data.update(approval_mode=group.approval_mode,extension_limit=group.extension_limit,unlimited=group.unlimited)
        data.update(overrides)
        return data

    def test_all_management_pages_use_portal_layout(self):
        paths = ['/management/', '/management/members/', '/management/members/new/',
                 f'/management/members/{self.a.pk}/', '/management/groups/', f'/management/groups/{self.default_group.pk}/',
                 '/management/storage/', '/management/images/', '/management/audits/', '/management/settings/']
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'manage-tabs')
                self.assertNotContains(response, 'href="/admin/')

    def test_student_cannot_open_or_modify_management(self):
        self.client.force_login(self.a)
        for path in ['/management/members/', f'/management/groups/{self.default_group.pk}/', '/management/settings/', '/management/images/']:
            self.assertEqual(self.client.get(path).status_code, 302)
            self.assertEqual(self.client.post(path, {}).status_code, 302)

    def test_create_member_hashes_password_and_assigns_role(self):
        response = self.client.post('/management/members/new/', self.member_data(username='new-member', password='Telescope-river-83729', group=str(self.longterm.pk), home_quota_gib=60))
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='new-member')
        self.assertTrue(user.check_password('Telescope-river-83729'))
        self.assertEqual(user.profile.group_id, self.longterm.pk)
        self.assertEqual(user.profile.home_quota_gib, 60)
        self.assertFalse(user.is_staff)

    def test_edit_member_reserves_actual_volume_growth(self):
        approve(task(self.a, self.image).pk, self.admin)
        response = self.client.post(f'/management/members/{self.a.pk}/', self.member_data(group=str(self.longterm.pk), home_quota_gib=60))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(VolumeAllocation.objects.get(user=self.a, kind='home').size_gib, 60)

    def test_invalid_shrink_rolls_back_other_member_changes(self):
        approve(task(self.a, self.image).pk, self.admin)
        response = self.client.post(f'/management/members/{self.a.pk}/', self.member_data(username='changed', group=str(self.longterm.pk), home_quota_gib=10))
        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.username, 'alice')
        self.assertEqual(self.a.profile.group_id, self.default_group.pk)

    def test_administrator_cannot_disable_own_access(self):
        response = self.client.post(f'/management/members/{self.admin.pk}/', self.member_data(username='admin', is_active='', is_staff=''))
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertTrue(self.admin.is_staff)

    def test_edit_policy_and_recycle_settings(self):
        policy = self.default_group
        data = {field: getattr(policy, field) for field in ('gpu_compute_percent', 'gpu_memory_gib', 'gpu_compute_per_card_percent', 'gpu_memory_per_card_gib', 'cpu', 'memory_gib', 'workspace_gib', 'home_gib', 'image_gib', 'concurrent_tasks', 'interactive_hours')}
        data['concurrent_tasks'] = 3
        data['name'] = policy.name
        data['approval_mode'] = policy.approval_mode
        data['extension_limit'] = policy.extension_limit
        self.assertEqual(self.client.post(f'/management/groups/{self.default_group.pk}/', data).status_code, 302)
        policy.refresh_from_db()
        self.assertEqual(policy.concurrent_tasks, 3)
        self.assertEqual(self.client.post('/management/settings/', {'workspace_retention_days': 21, 'snapshot_hours': 12, 'failed_snapshot_hours': 48}).status_code, 302)
        self.assertEqual(PlatformSettings.load().snapshot_hours, 12)
        self.assertEqual(self.client.post('/management/settings/', {'workspace_retention_days': 21, 'snapshot_hours': 0, 'failed_snapshot_hours': 48}).status_code, 200)
        self.assertEqual(PlatformSettings.load().snapshot_hours, 12)

    def test_image_action_is_owner_only_and_post_only(self):
        image = ImageArtifact.objects.create(owner=self.a, name='private', reference='private:1', verified=True, temporary=True)
        self.client.force_login(self.b)
        self.assertEqual(self.client.post(f'/images/{image.pk}/keep/').status_code, 404)
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(f'/images/{image.pk}/keep/').status_code, 405)
        self.assertEqual(self.client.post(f'/images/{image.pk}/keep/').status_code, 302)
        image.refresh_from_db()
        self.assertFalse(image.temporary)
