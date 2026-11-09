from copy import deepcopy
import json
from unittest.mock import patch

from django.test import TestCase

from portal.configuration import config
from portal.kube import KubeClient
from portal.registry import decode, encode, sync_registry_users
from tests.factories import fixtures


class RegistryKube:
    path = staticmethod(KubeClient.path)

    def __init__(self):
        self.namespace = config()['platform']['namespace']
        self.objects = {('secrets', self.namespace, 'registry-state'): {
            'data': {'users.json': encode('{}'), 'config.json': encode('{}')}}}
        self.patches = []

    def get(self, resource, namespace, name, group='v1'):
        return deepcopy(self.objects.get((resource, namespace, name)))

    def ensure(self, obj, resource, namespace, group='v1'):
        key = (resource, namespace, obj['metadata']['name'])
        if key not in self.objects:
            obj = deepcopy(obj)
            obj['metadata']['resourceVersion'] = '1'
            obj['data'] = {k: encode(v) for k, v in obj.pop('stringData', {}).items()}
            self.objects[key] = obj
        return self.get(resource, namespace, key[2])

    def call(self, method, path, body=None):
        if method == 'PUT':
            self.objects['secrets', self.namespace, 'registry-state'] = deepcopy(body)
        elif method == 'PATCH' and '/secrets/' in path:
            self.patches.append((path, deepcopy(body)))
            parts = path.split('/')
            obj = self.objects['secrets', parts[4], parts[6]]
            assert body['metadata']['resourceVersion'] == obj['metadata']['resourceVersion']
            obj['data'].update(body['data'])
            obj['metadata']['resourceVersion'] = str(int(obj['metadata']['resourceVersion']) + 1)


class RegistryCredentialTests(TestCase):
    def setUp(self):
        self.admin, self.user, self.other, self.image = fixtures()
        self.kube = RegistryKube()
        # Password uniqueness is real; expensive hash computation is unrelated to secret synchronization.
        self.hash_patch = patch('portal.registry.bcrypt.hashpw', return_value=b'test-hash')
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def test_reenabled_user_gets_current_credentials_in_both_namespaces(self):
        first = sync_registry_users(self.kube)[str(self.user.pk)]
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertNotIn(str(self.user.pk), sync_registry_users(self.kube))
        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        current = sync_registry_users(self.kube)[str(self.user.pk)]
        self.assertNotEqual(first['password'], current['password'])
        cfg = config()
        for ns in [cfg['platform']['workload_namespace'], cfg['platform']['build_namespace']]:
            secret = self.kube.get('secrets', ns, f'registry-u{self.user.pk}')
            auth = json.loads(decode(secret['data']['.dockerconfigjson']))['auths'][cfg['registry']['host']]['auth']
            self.assertEqual(decode(auth), current['login'] + ':' + current['password'])
        self.assertEqual(len(self.kube.patches), 2)
        sync_registry_users(self.kube)
        self.assertEqual(len(self.kube.patches), 2)

    def test_existing_secret_with_another_owner_is_never_updated(self):
        sync_registry_users(self.kube)
        ns = config()['platform']['workload_namespace']
        secret = self.kube.objects['secrets', ns, f'registry-u{self.user.pk}']
        secret['metadata']['labels']['platform.user'] = str(self.other.pk)
        with self.assertRaisesRegex(RuntimeError, '不属于本账号'):
            sync_registry_users(self.kube)
        self.assertFalse(self.kube.patches)

    def test_workspace_credentials_are_separate_read_only_identity(self):
        users=sync_registry_users(self.kube)
        identity=users[str(self.user.pk)]
        self.assertNotEqual(identity['reader']['password'],identity['password'])
        ns=config()['platform']['workload_namespace']
        secret=self.kube.get('secrets',ns,f'registry-u{self.user.pk}')
        value=json.loads(decode(secret['data']['docker-reader.json']))['auths'][config()['registry']['host']]['auth']
        self.assertEqual(decode(value),identity['reader']['login']+':'+identity['reader']['password'])
        self.assertNotEqual(secret['data']['docker-reader.json'],secret['data']['.dockerconfigjson'])
