"""Upgrade the previous syncdb schema without retaining a second runtime model."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from django.test import SimpleTestCase


class NodeMigrationTests(SimpleTestCase):
    def test_existing_data_is_attached_to_local_node_without_a_backup(self):
        root = Path(__file__).resolve().parents[2]
        program = '''import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
call_command('migrate', 'portal', '0001', verbosity=0, interactive=False)
apps = MigrationExecutor(connection).loader.project_state([('portal', '0001_initial')]).apps
user = apps.get_model('auth', 'User').objects.create(username='existing-user', password='unchanged-hash')
pool = apps.get_model('portal', 'StoragePool').objects.create(name='disk1', path='/data/volumes', storage_class='rawfile')
image = apps.get_model('portal', 'ImageArtifact').objects.create(name='existing-image', reference='registry/image:old', digest='sha256:'+'a'*64)
request = apps.get_model('portal', 'ResourceRequest').objects.create(user_id=user.pk, image_id=image.pk, purpose='existing-task', mode='batch')
volume = apps.get_model('portal', 'VolumeAllocation').objects.create(user_id=user.pk, request_id=request.pk, kind='work', pool_id=pool.pk, size_gib=20, pvc_name='existing-work')
# The old application used syncdb and has no portal migration records.
MigrationRecorder(connection).record_unapplied('portal', '0001_initial')
call_command('migrate', verbosity=0, interactive=False, fake_initial=True)
from portal.models import ComputeNode, StoragePool, ImageArtifact, ResourceRequest, VolumeAllocation
node = ComputeNode.objects.get(transport='local')
for model, pk in [(StoragePool,pool.pk),(ImageArtifact,image.pk),(ResourceRequest,request.pk),(VolumeAllocation,volume.pk)]:
    assert model.objects.get(pk=pk).node_id == node.pk
assert VolumeAllocation.objects.get(pk=volume.pk).pvc_name == 'existing-work'
from django.contrib.auth import get_user_model
assert get_user_model().objects.get(pk=user.pk).password == 'unchanged-hash'
print('Existing schema and resource ownership migrated successfully')
'''
        with tempfile.TemporaryDirectory(prefix='gpu-platform-migration-test-') as directory:
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings', 'DJANGO_DEBUG': '1',
                   'PLATFORM_DATA': directory, 'PLATFORM_CONFIG': str(root/'config.example.yaml'),
                   'PYTHONPATH': str(root/'backend'), 'PYTHONDONTWRITEBYTECODE': '1'}
            result = subprocess.run([sys.executable, '-c', program], cwd=root, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual([path.name for path in Path(directory).iterdir()], ['platform.sqlite3'])
