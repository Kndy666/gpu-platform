from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from portal.api_auth import issue_token
from portal.management_forms import PushSettingsForm
from portal.models import Notification, NotificationSettings
from portal.notifications import collect_expiry_reminders, deliver_one, encode_token
from portal.services import approve, audit
from tests.factories import fixtures, task


class NotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.alice, cls.bob, cls.image = fixtures()

    def configure(self, events=None):
        return NotificationSettings.objects.create(enabled=True, token_ciphertext=encode_token('a' * 32), events=events or ['submit'])

    def test_disabled_notifications_never_enqueue_or_make_network_calls(self):
        req = task(self.alice, self.image)
        audit(self.alice, 'submit', req.pk)
        with patch('portal.notifications.requests.post') as post:
            self.assertFalse(deliver_one())
        post.assert_not_called()
        self.assertEqual(Notification.objects.count(), 0)

    def test_event_selection_and_encrypted_token(self):
        config = self.configure()
        self.assertNotIn('a' * 32, config.token_ciphertext)
        req = task(self.alice, self.image)
        with patch('portal.notifications.requests.post') as post:
            audit(self.alice, 'submit', req.pk)
            audit(self.admin, 'approve', req.pk)
        post.assert_not_called()
        self.assertEqual(Notification.objects.count(), 1)
        message = Notification.objects.get()
        self.assertIn('alice', message.content)
        self.assertNotIn('a' * 32, message.content)

    def test_delivery_uses_https_and_enforces_five_requests_per_minute(self):
        config = self.configure()
        Notification.objects.create(event_key='1', event='submit', title='one', content='body')
        Notification.objects.create(event_key='2', event='submit', title='two', content='body')
        response = Mock()
        response.json.return_value = {'code': 200}
        with patch('portal.notifications.requests.post', return_value=response) as post:
            self.assertTrue(deliver_one())
            self.assertFalse(deliver_one())
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], 'https://www.pushplus.plus/send')
        self.assertEqual(Notification.objects.filter(state='sent').count(), 1)
        config.refresh_from_db()
        self.assertEqual(config.quota_used, 1)

    def test_daily_quota_stops_before_provider_and_errors_hide_tokens(self):
        config = self.configure()
        config.quota_date, config.quota_used = timezone.localdate(), 200
        config.save()
        Notification.objects.create(event_key='one', event='submit', title='test', content='body')
        with patch('portal.notifications.requests.post') as post:
            self.assertFalse(deliver_one())
        post.assert_not_called()
        config.quota_used = 0
        config.save()
        with patch('portal.notifications.requests.post', side_effect=RuntimeError('token=' + 'a' * 32)):
            self.assertTrue(deliver_one())
        message = Notification.objects.get()
        self.assertEqual(message.state, 'pending')
        self.assertEqual(message.error, 'RuntimeError')

    def test_expiry_warning_is_once_per_deadline_and_can_follow_an_extension(self):
        self.configure(['ending_soon'])
        req = task(self.alice, self.image, status='running', ends_at=timezone.now()+timedelta(minutes=20))
        from portal.models import Workspace
        Workspace.objects.create(request=req, object_name='test')
        collect_expiry_reminders()
        collect_expiry_reminders()
        self.assertEqual(Notification.objects.count(), 1)
        req.ends_at += timedelta(minutes=5)
        req.save(update_fields=['ends_at'])
        collect_expiry_reminders()
        self.assertEqual(Notification.objects.count(), 2)

    def test_api_never_returns_token_and_blank_keeps_it(self):
        config = self.configure()
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer '+issue_token(self.admin)['token']
        response = self.client.get('/api/v1/admin/notifications')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['data']['token_configured'])
        self.assertNotIn(config.token_ciphertext, response.content.decode())
        self.assertNotIn('a' * 32, response.content.decode())
        import json
        response = self.client.patch('/api/v1/admin/notifications', json.dumps({'token': '', 'events': ['finished']}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        config.refresh_from_db()
        self.assertEqual(config.events, ['finished'])
        form = PushSettingsForm({'enabled': True, 'events': ['submit'], 'token': 'bad', 'reminder_minutes': 30}, instance=config)
        self.assertFalse(form.is_valid())
