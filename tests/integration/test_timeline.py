from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from portal.models import ResourceRequest, Workspace
from portal.timeline import allocation_timeline
from tests.factories import fixtures, task


class TimelineTests(TestCase):
    def setUp(self):
        self.admin, self.user, self.other, self.image = fixtures()
        self.start = timezone.make_aware(datetime(2026, 11, 3))

    def workspace(self, req, **kwargs):
        return Workspace.objects.create(request=req, object_name='test-'+req.pk.hex, **kwargs)

    def test_extreme_or_invalid_dates_fall_back_to_today(self):
        self.client.force_login(self.user)
        for day in ('9999-12-31', '0001-01-01', '2026-02-30', 'invalid'):
            with self.subTest(day=day):
                response = self.client.get('/timeline/', {'date': day})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['date'], timezone.localdate().isoformat())

    def test_history_outside_day_is_not_loaded_into_python(self):
        old = [ResourceRequest(user=self.user, image=self.image, purpose='old', mode='batch', status='succeeded') for _ in range(100)]
        ResourceRequest.objects.bulk_create(old)
        Workspace.objects.bulk_create([
            Workspace(request=req, object_name='old-'+req.pk.hex, started_at=self.start-timedelta(days=3),
                      ended_at=self.start-timedelta(days=2), cleanup_complete=True) for req in old
        ])
        current = task(self.user, self.image, mode='batch', starts_at=None, ends_at=None, status='succeeded')
        self.workspace(current, started_at=self.start+timedelta(hours=1), ended_at=self.start+timedelta(hours=2))
        with patch.object(ResourceRequest, 'from_db', wraps=ResourceRequest.from_db) as hydrated:
            result = allocation_timeline(self.user, '2026-11-03')
        self.assertEqual(hydrated.call_count, 1)
        self.assertEqual({block['id'] for block in result['blocks']}, {str(current.pk)})

    def test_intervals_use_actual_end_and_exclude_touching_boundaries(self):
        crossing = task(self.user, self.image, starts_at=self.start-timedelta(hours=2),
                        ends_at=self.start+timedelta(hours=3), status='succeeded')
        self.workspace(crossing, started_at=self.start-timedelta(hours=1), ended_at=self.start+timedelta(hours=1))
        for started, ended in ((self.start-timedelta(hours=1), self.start),
                               (self.start+timedelta(days=1), self.start+timedelta(days=1, hours=1))):
            req = task(self.user, self.image, mode='batch', starts_at=None, ends_at=None, status='succeeded')
            self.workspace(req, started_at=started, ended_at=ended)
        reservation = task(self.user, self.image, status='scheduled', starts_at=self.start+timedelta(hours=5),
                           ends_at=self.start+timedelta(hours=6))
        self.workspace(reservation)
        result = allocation_timeline(self.user, '2026-11-03')
        self.assertEqual({block['id'] for block in result['blocks']}, {str(crossing.pk), str(reservation.pk)})
        actual = next(block for block in result['blocks'] if block['id'] == str(crossing.pk))
        self.assertEqual(actual['left'], 0)
        self.assertAlmostEqual(actual['width'], 100/24)
        self.assertEqual(next(block for block in result['blocks'] if block['id'] == str(reservation.pk))['row'], 'reservations')

    def test_open_program_has_no_allocation_in_future_day(self):
        req = task(self.user, self.image, mode='batch', starts_at=None, ends_at=None, status='running')
        self.workspace(req, started_at=self.start+timedelta(hours=1))
        with patch('portal.timeline.timezone.now', return_value=self.start+timedelta(hours=4)):
            today = allocation_timeline(self.user, '2026-11-03')
            future = allocation_timeline(self.user, '2026-11-04')
        self.assertTrue(today['blocks'])
        self.assertTrue(all(block['open'] and block['end'] is None for block in today['blocks']))
        self.assertEqual(future['blocks'], [])

    def test_batch_queue_with_no_start_time_is_visible_without_an_invented_interval(self):
        req = task(self.user, self.image, mode='batch', starts_at=None, ends_at=None, status='queued')
        self.workspace(req)
        with patch('portal.timeline.timezone.now', return_value=self.start+timedelta(hours=6)):
            result = allocation_timeline(self.user, '2026-11-03')
        queued = result['groups'][0]['queued']
        self.assertEqual([entry['id'] for entry in queued], [str(req.pk)])
        self.assertEqual(queued[0]['status_label'], '排队中')
        self.assertEqual(result['blocks'], [])
        self.assertEqual(result['groups'][0]['entries'], [])

    def test_queue_includes_preparation_and_startup_but_excludes_future_and_finished_work(self):
        pending = []
        for state in ('approved', 'preparing', 'queued', 'starting'):
            req = task(self.user, self.image, mode='batch', starts_at=None, ends_at=None, status=state)
            self.workspace(req)
            pending.append(str(req.pk))
        future = task(self.user, self.image, mode='batch', starts_at=self.start+timedelta(hours=12), ends_at=None, status='scheduled')
        self.workspace(future)
        finished = task(self.user, self.image, mode='batch', starts_at=None, ends_at=None, status='failed')
        self.workspace(finished, ended_at=self.start+timedelta(hours=1))
        expired = task(self.user, self.image, starts_at=self.start, ends_at=self.start+timedelta(hours=1), status='queued')
        self.workspace(expired)
        with patch('portal.timeline.timezone.now', return_value=self.start+timedelta(hours=6)):
            result = allocation_timeline(self.user, '2026-11-03')
            tomorrow = allocation_timeline(self.user, '2026-11-04')
        self.assertEqual({entry['id'] for entry in result['groups'][0]['queued']}, set(pending))
        self.assertEqual(tomorrow['groups'][0]['queued'], [])

    def test_queue_keeps_other_users_task_names_private(self):
        req = task(self.other, self.image, mode='batch', starts_at=None, ends_at=None, status='queued', purpose='private training')
        self.workspace(req)
        with patch('portal.timeline.timezone.now', return_value=self.start+timedelta(hours=6)):
            result = allocation_timeline(self.user, '2026-11-03')
        entry = result['groups'][0]['queued'][0]
        self.assertEqual(entry['title'], '排队任务')
        self.assertIsNone(entry['id'])
        self.assertIsNone(entry['url'])
