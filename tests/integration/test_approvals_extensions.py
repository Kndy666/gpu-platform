from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from portal.api_auth import issue_token
from portal.approvals import request_extension, review_extension, submit_request
from portal.management_forms import MemberForm
from portal.models import ExtensionRequest, UserGroup, VolumeAllocation, Workspace
from portal.policies import approval_mode, extension_limit
from portal.services import approve, policy_for, validate_request
from tests.factories import fixtures, task


class ApprovalExtensionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.alice, cls.bob, cls.image = fixtures()

    def environment(self):
        req = task(self.alice, self.image)
        approve(req.pk, self.admin)
        return req

    def auto(self, user=None):
        user = user or self.alice
        user.profile.approval_mode = 'automatic'
        user.profile.save(update_fields=['approval_mode'])

    def test_manual_extension_keeps_deadline_until_review_and_counts_success(self):
        req = self.environment()
        previous = req.ends_at
        entry = request_extension(req.pk, self.alice, previous + timedelta(hours=4), '继续实验')
        req.refresh_from_db()
        self.assertEqual((req.ends_at, req.extension_count), (previous, 0))
        self.assertEqual(entry.status, 'pending')
        review_extension(entry.pk, self.admin, 'approve')
        req.refresh_from_db()
        self.assertEqual((req.ends_at, req.extension_count), (entry.ends_at, 1))
        with self.assertRaisesRegex(ValidationError, '次数'):
            request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '再次延期')

    def test_rejection_does_not_consume_allowance_and_pending_is_unique(self):
        req = self.environment()
        entry = request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '继续')
        with self.assertRaisesRegex(ValidationError, '正在审批'):
            request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=2), '重复')
        review_extension(entry.pk, self.admin, 'reject', '预约资源不足')
        req.refresh_from_db()
        self.assertEqual(req.extension_count, 0)
        self.assertEqual(request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '重试').status, 'pending')

    def test_group_default_and_member_extension_override(self):
        req = self.environment()
        self.assertEqual(extension_limit(self.alice), 1)
        UserGroup.objects.filter(pk=self.alice.profile.group_id).update(extension_limit=0)
        with self.assertRaisesRegex(ValidationError, '次数'):
            request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '继续')
        self.alice.profile.extension_limit = 2
        self.alice.profile.save(update_fields=['extension_limit'])
        self.auto()
        entry = request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '继续')
        self.assertEqual(entry.status, 'approved')
        self.assertEqual(extension_limit(self.alice), 2)

    def test_extension_duration_matches_creation_limit(self):
        req = self.environment()
        hours = policy_for(self.alice).interactive_hours
        with self.assertRaisesRegex(ValidationError, '与新建环境一致'):
            request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=hours, seconds=1), '继续')
        self.assertEqual(request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=hours), '继续').status, 'pending')

    def test_other_user_stopped_environment_and_changed_deadline_cannot_extend(self):
        req = self.environment()
        with self.assertRaises(ValidationError):
            request_extension(req.pk, self.bob, req.ends_at + timedelta(hours=1), '继续')
        entry = request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '继续')
        Workspace.objects.filter(pk=req.pk).update(stop_requested=True)
        with self.assertRaises(ValidationError):
            review_extension(entry.pk, self.admin, 'approve')
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'pending')

    def test_unreachable_node_keeps_deadline_count_and_pending_approval(self):
        req = self.environment()
        Workspace.objects.filter(pk=req.pk).update(dispatch_started_at=timezone.now(), expire_at=req.ends_at)
        entry = request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '继续')
        with patch('portal.kube.KubeClient') as kube:
            kube.return_value.call.side_effect = RuntimeError('offline')
            with self.assertRaisesRegex(ValidationError, '未送达'):
                review_extension(entry.pk, self.admin, 'approve')
        req.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual((req.extension_count, entry.status), (0, 'pending'))
        self.assertEqual(req.workspace.expire_at, req.ends_at)

    def test_approved_extension_only_patches_deadline_on_running_pod(self):
        req = self.environment()
        Workspace.objects.filter(pk=req.pk).update(dispatch_started_at=timezone.now(), expire_at=req.ends_at)
        entry = request_extension(req.pk, self.alice, req.ends_at + timedelta(hours=1), '继续')
        with patch('portal.kube.KubeClient') as factory:
            review_extension(entry.pk, self.admin, 'approve')
        args = factory.return_value.call.call_args.args
        self.assertEqual(args[0], 'PATCH')
        self.assertEqual(args[2], {'metadata': {'annotations': {'platform.ends-at': entry.ends_at.isoformat()}}})
        factory.return_value.delete_owned.assert_not_called()

    def test_approval_inheritance_and_explicit_member_rule(self):
        group = self.alice.profile.group
        self.assertEqual(approval_mode(self.alice), 'manual')
        group.approval_mode = 'automatic'
        group.save(update_fields=['approval_mode'])
        req = task(self.alice, self.image)
        self.assertNotEqual(submit_request(req).status, 'submitted')
        self.assertTrue(VolumeAllocation.objects.filter(request=req).exists())
        self.alice.profile.approval_mode = 'manual'
        self.alice.profile.save(update_fields=['approval_mode'])
        second = task(self.alice, self.image)
        self.assertEqual(submit_request(second).status, 'submitted')

    def test_member_quota_overrides_are_independent_of_group_defaults(self):
        group = self.alice.profile.group
        self.alice.profile.quota_overrides = {'cpu': 200, 'memory_gib': 400}
        self.alice.profile.save(update_fields=['quota_overrides'])
        group.cpu = 8
        group.save(update_fields=['cpu'])
        self.assertEqual(policy_for(self.alice).cpu, 200)
        self.assertEqual(policy_for(self.bob).cpu, 8)
        self.assertEqual(policy_for(self.alice).memory_gib, 400)
        validate_request(task(self.alice, self.image, cpu=128, memory_gib=256))

    def test_member_editor_can_override_all_quotas_and_grow_home_above_group_default(self):
        group = self.alice.profile.group
        from portal.models import GROUP_QUOTA_FIELDS
        payload = {'username': 'alice', 'first_name': '', 'email': '', 'is_active': True, 'group': group.pk,
            'home_quota_gib': 80, 'inherit_quotas': False, 'unlimited': False, 'extension_limit': 3,
            **{name: getattr(group, name) for name in GROUP_QUOTA_FIELDS}}
        payload.update(home_gib=100, cpu=160)
        form = MemberForm(payload, actor=self.admin, member=self.alice)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(policy_for(self.alice).cpu, 160)
        self.assertEqual(policy_for(self.alice).home_gib, 100)
        self.assertEqual(extension_limit(self.alice), 3)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.profile.home_quota_gib, 80)

    def test_extension_api_ownership_idempotency_and_review(self):
        req = self.environment()
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.alice)['token']
        import json
        payload = json.dumps({'ends_at': (req.ends_at + timedelta(hours=1)).isoformat(), 'reason': '继续'})
        path = f'/api/v1/tasks/{req.pk}/extensions'
        first = self.client.post(path, payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY='extend-once')
        self.assertEqual(first.status_code, 201, first.content)
        again = self.client.post(path, payload, content_type='application/json', HTTP_IDEMPOTENCY_KEY='extend-once')
        self.assertEqual(first.json(), again.json())
        self.assertEqual(ExtensionRequest.objects.count(), 1)
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.bob)['token']
        self.assertEqual(self.client.get(path).status_code, 404)
        identifier = first.json()['data']['id']
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.admin)['token']
        response = self.client.post(f'/api/v1/extensions/{identifier}/review', json.dumps({'action': 'approve'}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)

    def test_unlimited_member_override_without_numeric_fields(self):
        payload = {'username': 'alice', 'first_name': '', 'email': '', 'is_active': True,
            'group': self.alice.profile.group_id, 'home_quota_gib': 50, 'unlimited': 'on'}
        form = MemberForm(payload, actor=self.admin, member=self.alice)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(policy_for(self.alice).unlimited)
        self.assertFalse(policy_for(self.bob).unlimited)

    def test_pending_request_can_be_approved_from_dedicated_tab(self):
        req = task(self.alice, self.image)
        self.client.force_login(self.admin)
        response = self.client.post('/management/approvals/', {'action': 'request', 'id': str(req.pk),
            'decision': 'approve', 'priority': '0', 'reason': ''})
        self.assertEqual(response.status_code, 302)
        req.refresh_from_db()
        self.assertNotEqual(req.status, 'submitted')

    def test_member_quota_api_partial_update_overrides_group_default(self):
        import json
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.admin)['token']
        response = self.client.patch(f'/api/v1/admin/members/{self.alice.pk}', json.dumps({'cpu': 160}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['data']['effective_limits']['cpu'], 160)
        self.assertEqual(policy_for(self.bob).cpu, self.bob.profile.group.cpu)

    def test_approval_page_and_config_api_preserve_access_control(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get('/management/approvals/').status_code, 302)
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.alice)['token']
        self.assertEqual(self.client.get('/api/v1/admin/approvals').status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/management/approvals/').status_code, 200)
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + issue_token(self.admin)['token']
        import json
        response = self.client.patch('/api/v1/admin/approvals', json.dumps({'scope': 'member', 'target': self.alice.pk, 'mode': 'automatic'}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(approval_mode(self.alice), 'automatic')

    def test_stale_reconciler_deadline_does_not_stop_an_extended_container(self):
        from portal.reconcile import Reconciler
        req = self.environment()
        req.ends_at = timezone.now() - timedelta(seconds=1)
        reconciler = Reconciler(kube=Mock(), node=req.node)
        with patch.object(reconciler, 'cleanup') as cleanup:
            reconciler.reconcile_task(req)
        cleanup.assert_not_called()
        req.refresh_from_db()
        self.assertIsNone(req.workspace.ended_at)
        self.assertGreater(req.ends_at, timezone.now())
