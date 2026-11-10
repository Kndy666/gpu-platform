import os
from pathlib import Path
from urllib.parse import quote
import requests

class KubeError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f'Kubernetes HTTP {status}: {message[:1000]}')

class KubeClient:
    def __init__(self, node=None):
        from .node_context import active_node
        self.node = node or active_node.get()
        self.base = os.environ.get('KUBE_API', 'https://kubernetes.default.svc').rstrip('/')
        self.token_file = Path(os.environ.get('KUBE_TOKEN_FILE', '/var/run/secrets/kubernetes.io/serviceaccount/token'))
        self.ca = os.environ.get('KUBE_CA', '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
        self.session = requests.Session()
        self.session.trust_env = False

    def call(self, method, path, body=None, params=None, text=False):
        if self.node and self.node.transport == 'ssh':
            from .node_transport import node_call
            response = node_call(self.node, 'kube', method=method, path=path, body=body, params=params, text=text)
            if response['status'] == 404:
                return None
            if response['status'] >= 400:
                raise KubeError(response['status'], str(response['data']))
            return response['data']
        headers = {'Authorization': 'Bearer ' + self.token_file.read_text().strip()}
        if method == 'PATCH':
            headers['Content-Type'] = 'application/merge-patch+json'
        r = self.session.request(method, self.base + path, json=body, params=params, headers=headers, verify=self.ca, timeout=(5, 30))
        if r.status_code == 404:
            return None
        if not r.ok:
            raise KubeError(r.status_code, r.text)
        return r.text if text else r.json()

    @staticmethod
    def path(resource, namespace=None, name=None, group='v1'):
        prefix = '/api/v1' if group == 'v1' else '/apis/' + group
        return prefix + ('/namespaces/' + quote(namespace, safe='') if namespace else '') + '/' + resource + ('/' + quote(name, safe='') if name else '')

    def get(self, resource, ns, name, group='v1'):
        return self.call('GET', self.path(resource, ns, name, group))

    def list(self, resource, ns=None, selector=None, group='v1'):
        return (self.call('GET', self.path(resource, ns, group=group), params={'labelSelector': selector} if selector else {}) or {}).get('items', [])

    def volume_stats(self, node):
        if self.node and self.node.transport == 'ssh':
            from .node_transport import node_call
            return node_call(self.node, 'volume_stats', node=node['metadata']['name'])
        address = next(a['address'] for a in node['status']['addresses'] if a['type'] == 'InternalIP')
        if ':' in address: address = '['+address+']'
        port = node['status'].get('daemonEndpoints', {}).get('kubeletEndpoint', {}).get('Port', 10250)
        response = self.session.get(f'https://{address}:{port}/stats/summary',
            headers={'Authorization': 'Bearer '+self.token_file.read_text().strip()}, verify=self.ca, timeout=(2, 5))
        response.raise_for_status()
        return response.json()

    def ensure(self, obj, resource, ns, group='v1'):
        name = obj['metadata']['name']
        existing = self.get(resource, ns, name, group)
        if existing:
            expected = obj['metadata'].get('labels', {})
            if any(existing['metadata'].get('labels', {}).get(k) != v for k, v in expected.items() if k.startswith('platform.')):
                raise RuntimeError(f'拒绝接管非平台对象 {resource}/{name}')
            return existing
        try:
            return self.call('POST', self.path(resource, ns, group=group), obj)
        except KubeError as e:
            if e.status != 409:
                raise
            existing = self.get(resource, ns, name, group)
            if not existing:
                raise
            if any(existing['metadata'].get('labels', {}).get(k) != v for k, v in obj['metadata'].get('labels', {}).items() if k.startswith('platform.')):
                raise RuntimeError('对象竞争时发现所有权冲突') from e
            return existing

    def delete_owned(self, resource, ns, name, owner, group='v1'):
        current = self.get(resource, ns, name, group)
        if not current:
            return True
        if current['metadata'].get('labels', {}).get('platform.task') != str(owner):
            raise RuntimeError('拒绝删除不属于本任务的对象')
        self.call('DELETE', self.path(resource, ns, name, group), {
            'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Foreground',
            'preconditions': {'uid': current['metadata']['uid']}})
        return False
