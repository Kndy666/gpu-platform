import base64
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from portal.configuration import config
from portal.image_lifecycle import collect_images, keep_image, prepare_collection, recycle_image
from portal.models import ImageArtifact, PlatformSettings, Workspace
from tests.factories import fixtures, task


class ImageLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.a, cls.b, cls.base = fixtures()

    def setUp(self):
        self.image = ImageArtifact.objects.create(owner=self.a, name='snapshot',
            reference=config()['registry']['host']+f'/user-{self.a.pk}/environment:version',
            digest='sha256:'+'b'*64, verified=True, temporary=True, size_bytes=1024)
        self.kube = Mock()
        self.kube.get.return_value = {'data': {'collector.json': base64.b64encode(json.dumps({'login': 'gc', 'password': 'test'}).encode()).decode()}}

    def expire(self):
        self.image.recycle_after = timezone.now() - timedelta(hours=1)
        self.image.save()

    def test_unfinished_requests_hold_image_and_reset_countdown(self):
        for status in ('submitted', 'approved', 'preparing', 'queued', 'starting', 'running'):
            with self.subTest(status=status):
                self.expire()
                req = task(self.a, self.image, status=status)
                self.assertIsNone(prepare_collection(self.image.pk))
                self.image.refresh_from_db()
                self.assertIsNone(self.image.recycle_after)
                req.delete()

    def test_terminal_request_waits_for_actual_resource_release(self):
        req = task(self.a, self.image, status='succeeded')
        ws = Workspace.objects.create(request=req, object_name='hold')
        self.expire()
        self.assertIsNone(prepare_collection(self.image.pk))
        ws.cleanup_complete = True
        ws.save()
        self.assertIsNone(prepare_collection(self.image.pk))
        self.image.refresh_from_db()
        self.assertAlmostEqual((self.image.recycle_after-timezone.now()).total_seconds(), 24*3600, delta=5)

    def test_failed_program_gets_longer_retention(self):
        task(self.a, self.image, status='failed')
        prepare_collection(self.image.pk)
        self.image.refresh_from_db()
        self.assertAlmostEqual((self.image.recycle_after-timezone.now()).total_seconds(), 72*3600, delta=5)

    def test_keep_converts_snapshot_to_permanent_image(self):
        self.expire()
        keep_image(self.image.pk, self.a)
        self.assertIsNone(prepare_collection(self.image.pk))
        self.image.refresh_from_db()
        self.assertFalse(self.image.temporary)
        self.assertIsNone(self.image.recycle_after)

    def test_shared_and_permanent_images_are_not_automatically_collected(self):
        self.image.temporary = False
        self.image.save()
        self.assertIsNone(prepare_collection(self.image.pk))
        with self.assertRaises(ValidationError):
            recycle_image(self.base.pk, self.admin)

    def test_manual_recycle_has_undo_window_and_checks_usage(self):
        self.image.temporary = False
        self.image.save()
        recycle_image(self.image.pk, self.a)
        self.image.refresh_from_db()
        self.assertAlmostEqual((self.image.recycle_after-timezone.now()).total_seconds(), 24*3600, delta=5)
        req = task(self.a, self.image)
        with self.assertRaises(ValidationError):
            recycle_image(self.image.pk, self.a)
        self.assertTrue(req.image.verified)

    def test_expired_snapshot_removes_only_tag_and_retains_history(self):
        req = task(self.a, self.image, status='succeeded')
        self.expire()
        with patch('portal.image_lifecycle.RegistryClient') as client:
            collect_images(self.kube)
            client.return_value.delete_tag.assert_called_once_with(f'user-{self.a.pk}/environment', 'version')
        self.image.refresh_from_db()
        self.assertEqual(self.image.state, 'deleted')
        self.assertFalse(self.image.verified)
        self.assertIsNotNone(self.image.deleted_at)
        req.refresh_from_db()
        self.assertEqual(req.image_id, self.image.pk)

    def test_failed_deletion_retries_without_reopening_image_for_new_tasks(self):
        self.expire()
        with patch('portal.image_lifecycle.RegistryClient') as client:
            client.return_value.delete_tag.side_effect = RuntimeError('registry unavailable')
            collect_images(self.kube)
        self.image.refresh_from_db()
        self.assertEqual(self.image.state, 'deleting')
        self.assertFalse(self.image.verified)
        self.assertTrue(self.image.cleanup_error)
        self.image.cleanup_retry_at = timezone.now()-timedelta(minutes=1)
        self.image.save()
        with patch('portal.image_lifecycle.RegistryClient') as client:
            collect_images(self.kube)
        self.image.refresh_from_db()
        self.assertEqual(self.image.state, 'deleted')

    def test_other_users_cannot_keep_or_recycle_image(self):
        for operation in (keep_image, recycle_image):
            with self.assertRaises(ValidationError):
                operation(self.image.pk, self.b)

    def test_deadline_uses_current_administrator_settings(self):
        settings = PlatformSettings.load()
        settings.snapshot_hours = 3
        settings.save()
        prepare_collection(self.image.pk)
        self.image.refresh_from_db()
        self.assertAlmostEqual((self.image.recycle_after-timezone.now()).total_seconds(), 3*3600, delta=5)

    def test_collector_can_delete_but_cannot_read_or_push_other_users_images(self):
        from portal.registry import zot_config
        cfg = zot_config({str(self.a.pk): {'login': 'alice', 'reader': {'login': 'alice-reader'}}}, {'login': 'collector'})
        repos = cfg['http']['accessControl']['repositories']
        self.assertNotIn('user-*/**', repos)
        self.assertEqual(repos[f'user-{self.b.pk}/**']['policies'], [{'users': ['collector'], 'actions': ['delete']}])
        self.assertIn({'users': ['collector'], 'actions': ['delete']}, repos[f'user-{self.a.pk}/**']['policies'])

    def test_delete_tag_treats_already_removed_tag_as_success(self):
        from portal.registry import RegistryClient
        client = RegistryClient({'login': 'gc', 'password': 'test'})
        client.session = Mock()
        client.session.delete.return_value.status_code = 404
        client.delete_tag('user-2/environment', 'version')
        self.assertTrue(client.session.delete.call_args.args[0].endswith('/manifests/version'))
        client.session.delete.return_value.raise_for_status.assert_not_called()
