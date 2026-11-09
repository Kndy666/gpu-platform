from unittest.mock import patch
from django.test import TestCase
from portal.forms import ImageBuildForm
from portal.models import ImageBuild
from portal.builds import build_objects
from portal.registry import zot_config
from tests.factories import fixtures

class ImageTests(TestCase):
    def setUp(self): self.admin, self.a, self.b, self.image = fixtures()

    def test_registry_default_deny_and_per_user_repository(self):
        users = {str(u.pk): {'login': f'user-{u.pk}', 'reader': {'login': f'user-{u.pk}-reader'}} for u in [self.a,self.b]}
        repositories = zot_config(users)['http']['accessControl']['repositories']
        self.assertEqual(repositories['**']['defaultPolicy'], [])
        self.assertEqual(repositories[f'user-{self.a.pk}/**']['policies'][0]['users'], [f'user-{self.a.pk}'])
        self.assertNotIn(f'user-{self.b.pk}', repositories[f'user-{self.a.pk}/**']['policies'][0]['users'])
        self.assertEqual(repositories[f'user-{self.a.pk}/**']['policies'][1],{'users':[f'user-{self.a.pk}-reader'],'actions':['read']})

    def test_build_uses_only_own_credentials_without_home_or_host_socket(self):
        build = ImageBuild.objects.create(user=self.a, name='experiment', dockerfile='FROM test')
        cm, job, repo, reference = build_objects(build)
        spec = job['spec']['template']['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertEqual(repo, f'user-{self.a.pk}/experiment')
        secrets = [v['secret']['secretName'] for v in spec['volumes'] if 'secret' in v]
        self.assertEqual(secrets, [f'registry-u{self.a.pk}'])
        self.assertFalse(any('hostPath' in v or 'persistentVolumeClaim' in v for v in spec['volumes']))
        self.assertNotIn('privileged', spec['containers'][0]['securityContext'])

    def test_context_allows_only_configured_public_https_git_hosts(self):
        for url in ['https://127.0.0.1/project', 'https://evil.example/project', 'http://github.com/org/repo', 'https://user:password@github.com/org/repo']:
            form = ImageBuildForm({'name': 'experiment', 'dockerfile': 'FROM test', 'context_git': url})
            self.assertFalse(form.is_valid(), url)
        form = ImageBuildForm({'name': 'experiment', 'dockerfile': 'FROM test', 'context_git': 'https://github.com/org/repo.git'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_bootstrap_uses_bundled_context_and_requires_superuser(self):
        build = ImageBuild(user=self.admin, name='base', dockerfile='COPY runner.sh /runner.sh', bootstrap=True)
        cm, job, _, _ = build_objects(build)
        self.assertIn('runner.sh', cm['data'])
        self.assertIn('emptyDir', job['spec']['template']['spec']['volumes'][0])
        self.assertIn('cp -L', job['spec']['template']['spec']['initContainers'][0]['command'][2])
        build.user = self.a
        with self.assertRaises(RuntimeError): build_objects(build)

    def test_bootstrap_includes_docker_startup(self):
        build=ImageBuild(user=self.admin,name='platform-ubuntu-ssh',dockerfile='COPY docker.sh /opt/platform/docker.sh',bootstrap=True)
        cm,job,_,_=build_objects(build)
        self.assertIn('docker.sh',cm['data'])
        self.assertIn('runner.sh',cm['data'])
        self.assertIn('docker/certs.d',cm['data']['docker.sh'])
        self.assertIn('ble-0.4.0-devel3.tar.xz',cm['binaryData'])
        command=job['spec']['template']['spec']['containers'][0]['command']
        self.assertTrue(any(item.startswith('build-arg:BASE_IMAGE=') for item in command))
