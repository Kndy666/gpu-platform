from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from portal.models import UserGroup, UserProfile
from portal.services import policy_for
from tests.factories import fixtures, member_form_data


class GroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.a, cls.b, cls.image = fixtures()

    def setUp(self):
        self.client.force_login(self.admin)
        self.default = UserGroup.objects.get(is_default=True)

    def group_data(self, **overrides):
        data = {field: getattr(self.default, field) for field in ['gpu_compute_percent','gpu_memory_gib','gpu_compute_per_card_percent','gpu_memory_per_card_gib','cpu','memory_gib','workspace_gib','home_gib','image_gib','concurrent_tasks','interactive_hours']}
        data.update(name='推理用户',approval_mode='manual',extension_limit=1)
        data.update(overrides)
        return data

    def test_create_named_group_and_assign_member(self):
        response = self.client.post('/management/groups/new/', self.group_data(gpu_compute_percent=100))
        self.assertEqual(response.status_code, 302)
        group = UserGroup.objects.get(name='推理用户')
        self.assertEqual(group.gpu_compute_percent, 100)
        response = self.client.post(f'/management/members/{self.a.pk}/', member_form_data(self.a,group=group.pk))
        self.assertEqual(response.status_code, 302)
        self.a.refresh_from_db()
        self.assertEqual(policy_for(self.a).pk, group.pk)

    def test_gpu_quota_table_renders_and_saves_all_four_limits(self):
        url=f'/management/groups/{self.default.pk}/'
        page=self.client.get(url)
        for name in ['gpu_compute_percent','gpu_memory_gib','gpu_compute_per_card_percent','gpu_memory_per_card_gib']:
            self.assertContains(page,f'id="id_{name}"')
        data=self.group_data(name='临时用户',gpu_compute_percent=75,gpu_memory_gib=6,
                             gpu_compute_per_card_percent=50,gpu_memory_per_card_gib=4)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.default.refresh_from_db()
        self.assertEqual((self.default.gpu_compute_percent,self.default.gpu_memory_gib,
                          self.default.gpu_compute_per_card_percent,self.default.gpu_memory_per_card_gib),(75,6,50,4))

    def test_new_default_group_is_used_by_future_members(self):
        self.assertEqual(self.client.post('/management/groups/new/', self.group_data(make_default='on')).status_code, 302)
        group = UserGroup.objects.get(is_default=True)
        self.assertEqual(group.name, '推理用户')
        user = get_user_model().objects.create_user('new-default-user')
        self.assertEqual(user.profile.group_id, group.pk)
        self.assertEqual(UserGroup.objects.filter(is_default=True).count(), 1)

    def test_delete_group_moves_members_and_updates_default(self):
        destination = UserGroup.objects.create(name='目标组',home_gib=100)
        response = self.client.post(f'/management/groups/{self.default.pk}/delete/', {'destination':destination.pk})
        self.assertEqual(response.status_code,302)
        self.assertFalse(UserGroup.objects.filter(pk=self.default.pk).exists())
        self.assertEqual(UserProfile.objects.filter(group=destination).count(),3)
        destination.refresh_from_db()
        self.assertTrue(destination.is_default)

    def test_cannot_delete_last_group(self):
        response = self.client.post(f'/management/groups/{self.default.pk}/delete/', {})
        self.assertEqual(response.status_code,200)
        self.assertTrue(UserGroup.objects.filter(pk=self.default.pk).exists())

    def test_delete_preserves_member_settings_when_destination_defaults_are_lower(self):
        destination = UserGroup.objects.create(name='小组',home_gib=10)
        response = self.client.post(f'/management/groups/{self.default.pk}/delete/', {'destination':destination.pk})
        self.assertEqual(response.status_code,302)
        self.assertEqual(UserProfile.objects.filter(group=destination).count(),3)
        self.assertEqual(policy_for(self.a).home_gib,50)

    def test_deleted_seed_group_does_not_reappear_after_bootstrap(self):
        empty = UserGroup.objects.create(name='长期用户')
        response = self.client.post(f'/management/groups/{empty.pk}/delete/', {})
        self.assertEqual(response.status_code,302)
        call_command('bootstrap',verbosity=0)
        self.assertFalse(UserGroup.objects.filter(name='长期用户').exists())

    def test_students_cannot_change_groups(self):
        self.client.force_login(self.a)
        self.assertEqual(self.client.post('/management/groups/new/',self.group_data()).status_code,302)
        self.assertFalse(UserGroup.objects.filter(name='推理用户').exists())

    def test_group_defaults_change_does_not_lower_existing_member_capacity(self):
        response = self.client.post(f'/management/groups/{self.default.pk}/', self.group_data(home_gib=10))
        self.assertEqual(response.status_code, 302)
        self.default.refresh_from_db()
        self.assertEqual(self.default.home_gib, 10)
        self.assertEqual(policy_for(self.a).home_gib, 50)
