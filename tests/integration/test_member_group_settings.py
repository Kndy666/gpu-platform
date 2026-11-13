import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from portal.api_auth import issue_token
from portal.models import GROUP_QUOTA_FIELDS, UserGroup
from portal.policies import approval_mode, extension_limit
from portal.services import policy_for
from tests.factories import fixtures, task, member_form_data


class MemberGroupSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.alice, cls.bob, cls.image = fixtures()
        cls.group = cls.alice.profile.group

    def setUp(self):
        self.client.force_login(self.admin)
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.admin)['token']

    def test_both_editors_have_the_same_editable_policy_fields(self):
        member = self.client.get(f'/management/members/{self.alice.pk}/')
        group = self.client.get(f'/management/groups/{self.group.pk}/')
        fields = [*GROUP_QUOTA_FIELDS, 'unlimited', 'approval_mode', 'extension_limit']
        for page in (member, group):
            self.assertEqual(page.status_code, 200)
            for name in fields:
                self.assertFalse(page.context['form'].fields[name].disabled, name)

    def test_new_member_copies_group_defaults_once(self):
        self.group.approval_mode = 'automatic'
        self.group.extension_limit = 3
        self.group.cpu = 48
        self.group.save()
        payload = {'username': 'new-member', 'password': 'Orbit-telescope-93271', 'group': self.group.pk}
        response = self.client.post('/api/v1/admin/members', json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        user = get_user_model().objects.get(username='new-member')
        self.assertEqual(policy_for(user).cpu, 48)
        self.assertEqual(approval_mode(user), 'automatic')
        self.assertEqual(extension_limit(user), 3)
        self.group.cpu = 8
        self.group.approval_mode = 'manual'
        self.group.extension_limit = 0
        self.group.save()
        self.assertEqual((policy_for(user).cpu, approval_mode(user), extension_limit(user)), (48, 'automatic', 3))
        self.assertEqual((policy_for(self.alice).cpu, approval_mode(self.alice), extension_limit(self.alice)), (80, 'manual', 1))

    def test_member_edit_saves_all_settings_without_modifying_group_or_peers(self):
        values = member_form_data(self.alice, cpu=160, approval_mode='automatic', extension_limit=4)
        response = self.client.post(f'/management/members/{self.alice.pk}/', values)
        self.assertEqual(response.status_code, 302)
        self.assertEqual((policy_for(self.alice).cpu, approval_mode(self.alice), extension_limit(self.alice)), (160, 'automatic', 4))
        self.assertEqual((policy_for(self.bob).cpu, approval_mode(self.bob), extension_limit(self.bob)), (80, 'manual', 1))
        self.group.refresh_from_db()
        self.assertEqual((self.group.cpu, self.group.approval_mode, self.group.extension_limit), (80, 'manual', 1))

    def test_pending_requests_are_listed_in_web_and_api(self):
        req = task(self.alice, self.image)
        page = self.client.get('/management/approvals/')
        self.assertEqual([item.pk for item in page.context['pending']], [req.pk])
        queue = self.client.get('/api/v1/admin/approvals').json()['data']
        self.assertEqual([item['id'] for item in queue['items']], [str(req.pk)])

    def test_explicit_numeric_limits_are_not_ignored_for_an_unlimited_group(self):
        self.group.unlimited = True
        self.group.save()
        response = self.client.post('/api/v1/admin/members', json.dumps({'username': 'limited-member',
            'password': 'Orbit-telescope-93271', 'group': self.group.pk, 'cpu': 16}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username='limited-member').exists())
