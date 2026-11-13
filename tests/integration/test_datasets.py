import os
from pathlib import Path
import tempfile

from django.test import TestCase, override_settings
from tests.factories import fixtures


class DatasetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin,cls.a,cls.b,cls.image=fixtures()

    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root=Path(directory.name)/'public';self.root.mkdir()
        (self.root/'样本').mkdir();(self.root/'样本/data.txt').write_text('sample-content')
        (self.root/'readme.txt').write_text('public-readme')
        (Path(directory.name)/'private.txt').write_text('outside-secret')
        (self.root/'escape').symlink_to(Path(directory.name)/'private.txt')
        override=override_settings(PLATFORM_DATASET_ROOT=self.root);override.enable();self.addCleanup(override.disable)
        self.client.force_login(self.a)

    def test_list_nested_directories_and_filter(self):
        response=self.client.get('/datasets/')
        self.assertContains(response,'样本');self.assertContains(response,'readme.txt')
        self.assertNotContains(response,'outside-secret')
        self.assertContains(self.client.get('/datasets/',{'path':'样本'}),'data.txt')
        response=self.client.get('/datasets/',{'q':'README'})
        self.assertContains(response,'readme.txt');self.assertNotContains(response,'>样本</a>')

    def test_file_download_uses_attachment(self):
        response=self.client.get('/datasets/',{'path':'样本/data.txt'})
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content),b'sample-content')

    def test_traversal_and_symlinks_are_not_opened(self):
        for path in ['../private.txt','/etc/passwd','escape','样本/../../private.txt','\x00']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get('/datasets/',{'path':path}).status_code,404)

    def test_special_files_do_not_block_requests(self):
        os.mkfifo(self.root/'pipe')
        self.assertEqual(self.client.get('/datasets/',{'path':'pipe'}).status_code,404)

    def test_upload_is_not_available(self):
        self.assertEqual(self.client.post('/datasets/',{'name':'new.txt'}).status_code,405)
        self.assertFalse((self.root/'new.txt').exists())

    def test_auth_and_management_access(self):
        self.assertEqual(self.client.get('/management/datasets/').status_code,403)
        self.client.force_login(self.admin)
        response=self.client.get('/management/datasets/')
        self.assertEqual(response.status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get('/datasets/').status_code,302)
