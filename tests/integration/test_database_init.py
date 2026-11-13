import os
from pathlib import Path
import subprocess
import tempfile

from django.test import SimpleTestCase


class DatabaseInitializationTests(SimpleTestCase):
    def test_current_models_initialize_once_and_preserve_existing_data(self):
        root = Path(__file__).resolve().parents[2]
        program = '''
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.db import connection
call_command('initdb')
user=get_user_model().objects.create_user('sample', password='sample-password')
call_command('initdb')
assert get_user_model().objects.filter(pk=user.pk).exists()
assert user.profile.group.extension_limit==1
assert 'portal_extensionrequest' in connection.introspection.table_names()
assert 'portal_notification' in connection.introspection.table_names()
'''
        with tempfile.TemporaryDirectory(prefix='gpu-platform-init-test-') as directory:
            result = subprocess.run(['.venv/bin/python', '-c', program], cwd=root, capture_output=True, text=True,
                timeout=30, env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings', 'DJANGO_DEBUG': '1',
                                 'PLATFORM_DATA': directory, 'PYTHONPATH': str(root / 'backend')})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
