import base64
import hashlib
import json
import secrets
from urllib.parse import quote
import bcrypt
import requests
from django.contrib.auth import get_user_model
from .configuration import config
from .models import UserProfile

def encode(data): return base64.b64encode(data.encode()).decode()
def decode(data): return base64.b64decode(data).decode()

def zot_config(users, collector=None):
    repos = {'**': {'defaultPolicy': []}, 'shared/**': {'defaultPolicy': ['read']}}
    cleanup = [{'users': [collector['login']], 'actions': ['delete']}] if collector else []
    if cleanup:
        for pk in UserProfile.objects.values_list('user_id', flat=True):
            repos[f'user-{pk}/**'] = {'policies': cleanup, 'defaultPolicy': []}
    for pk, user in users.items():
        repos[f'user-{pk}/**'] = {'policies': [
            {'users': [user['login']], 'actions': ['read', 'create', 'update', 'delete']},
            {'users': [user['reader']['login']], 'actions': ['read']}] + cleanup, 'defaultPolicy': []}
    from .models import ImageArtifact
    prefix = config()['registry']['host']+'/'
    for image in ImageArtifact.objects.filter(shared=True, verified=True, owner__isnull=False):
        if not image.reference.startswith(prefix): continue
        repo = image.reference[len(prefix):].split('@')[0]
        if ':' in repo.rsplit('/', 1)[-1]: repo = repo.rsplit(':', 1)[0]
        identity = users.get(str(image.owner_id))
        if identity:
            repos[repo] = {'policies': [{'users': [identity['login']], 'actions': ['read', 'create', 'update', 'delete']}] + cleanup, 'defaultPolicy': ['read']}
    return {'distSpecVersion': '1.1.1', 'storage': {'rootDirectory': '/var/lib/registry', 'gc': True, 'gcDelay': '1h', 'gcInterval': '1h'},
            'http': {'address': '0.0.0.0', 'port': '5000', 'tls': {'cert': '/etc/zot/tls/tls.crt', 'key': '/etc/zot/tls/tls.key'},
                     'auth': {'htpasswd': {'path': '/etc/zot/auth/htpasswd'}}, 'accessControl': {'repositories': repos}},
            'log': {'level': 'info'}}

def sync_registry_users(kube):
    cfg = config(); ns = cfg['platform']['namespace']; host = cfg['registry']['host']
    state = kube.get('secrets', ns, 'registry-state')
    if not state: raise RuntimeError('等待仓库初始化')
    users = json.loads(decode(state['data']['users.json']))
    previous = json.dumps(users, sort_keys=True)
    collector = json.loads(decode(state['data']['collector.json'])) if state['data'].get('collector.json') else None
    if collector is None:
        password = secrets.token_urlsafe(32)
        collector = {'login': 'platform-collector', 'password': password, 'hash': bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()}
    active = list(get_user_model().objects.filter(is_active=True))
    active_ids = {str(u.pk) for u in active}
    users = {pk: v for pk, v in users.items() if pk in active_ids}
    for user in active:
        pk = str(user.pk)
        if pk not in users:
            password = secrets.token_urlsafe(32)
            users[pk] = {'login': f'user-{pk}', 'password': password,
                         'hash': bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()}
        if 'reader' not in users[pk]:
            password = secrets.token_urlsafe(32)
            users[pk]['reader'] = {'login': f'user-{pk}-reader', 'password': password,
                                   'hash': bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()}
    desired_config = json.dumps(zot_config(users, collector))
    changed = previous != json.dumps(users, sort_keys=True) or decode(state['data'].get('config.json', 'e30=')) != desired_config
    if changed:
        state['data'] = {'users.json': encode(json.dumps(users)), 'collector.json': encode(json.dumps(collector)),
                         'htpasswd': encode('\n'.join(f"{v['login']}:{v['hash']}" for v in [*users.values(), *[u['reader'] for u in users.values()], collector])+'\n'),
                         'config.json': encode(desired_config)}
        kube.call('PUT', kube.path('secrets', ns, 'registry-state'), state)
        digest = hashlib.sha256((json.dumps(users, sort_keys=True)+desired_config).encode()).hexdigest()
        kube.call('PATCH', kube.path('deployments', ns, 'registry', 'apps/v1'), {'spec': {'template': {'metadata': {'annotations': {'platform.registry-users': digest}}}}})
    for user in active:
        identity = users[str(user.pk)]
        auth = {'auths': {host: {'auth': encode(identity['login']+':'+identity['password'])}}}
        reader = identity['reader']
        reader_auth = {'auths': {host: {'auth': encode(reader['login']+':'+reader['password'])}}}
        name = f'registry-u{user.pk}'
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'labels': {'platform.user': str(user.pk)}},
                  'type': 'kubernetes.io/dockerconfigjson', 'stringData': {
                      '.dockerconfigjson': json.dumps(auth), 'docker-reader.json': json.dumps(reader_auth)}}
        for target in [cfg['platform']['workload_namespace'], cfg['platform']['build_namespace']]:
            current = kube.ensure(secret, 'secrets', target)
            if current['metadata'].get('labels', {}).get('platform.user') != str(user.pk):
                raise RuntimeError('拒绝更新不属于本账号的仓库凭证')
            expected = {key: encode(value) for key, value in secret['stringData'].items()}
            if any(current.get('data', {}).get(key) != value for key, value in expected.items()):
                kube.call('PATCH', kube.path('secrets', target, name),
                          {'metadata': {'resourceVersion': current['metadata']['resourceVersion']},
                           'data': expected})
        UserProfile.objects.filter(user=user).update(registry_secret=name)
    return users

class RegistryClient:
    def __init__(self, identity):
        from .node_context import active_node
        self.node = active_node.get()
        self.identity = identity
        self.host = config()['registry']['host']
        self.session = requests.Session(); self.session.trust_env = False
        self.session.auth = identity['login'], identity['password']
        self.session.verify = '/etc/platform-registry-ca/tls.crt'

    def request(self, method, url, **kwargs):
        if self.node and self.node.transport == 'ssh':
            from .node_transport import node_call
            result = node_call(self.node, 'registry', method=method, path=url.split(self.host, 1)[1],
                               identity=self.identity, headers=kwargs.get('headers', {}))
            response = requests.Response()
            response.status_code = result['status']
            response._content = result['data'].encode()
            response.headers.update(result.get('headers', {}))
            return response
        return getattr(self.session, method.lower())(url, **kwargs)

    def manifest(self, repo, reference):
        response = self.request('GET', f'https://{self.host}/v2/{quote(repo, safe="/")}/manifests/{quote(reference, safe=":")}',
            headers={'Accept': 'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'}, timeout=30)
        response.raise_for_status()
        content = response.json()
        if 'manifests' in content:
            arch = config()['platform']['architecture']
            target = next((m for m in content['manifests'] if m.get('platform', {}).get('architecture') == arch), None)
            if not target: raise RuntimeError('镜像缺少目标架构')
            return self.manifest(repo, target['digest'])
        digest = response.headers.get('Docker-Content-Digest', 'sha256:'+hashlib.sha256(response.content).hexdigest())
        size = sum(layer['size'] for layer in content.get('layers', [])) + content.get('config', {}).get('size', 0)
        return digest, size

    def delete_manifest(self, repo, digest):
        response = self.request('DELETE', f'https://{self.host}/v2/{quote(repo, safe="/")}/manifests/{quote(digest, safe=":")}', timeout=30)
        response.raise_for_status()

    def delete_tag(self, repo, tag):
        # Deleting the tag preserves other tags that share the same digest.
        response = self.request('DELETE', f'https://{self.host}/v2/{quote(repo, safe="/")}/manifests/{quote(tag, safe="")}', timeout=30)
        if response.status_code != 404:
            response.raise_for_status()
