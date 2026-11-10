#!/usr/bin/env python3
"""Local privileged worker endpoint. SSH users reach it through its Unix socket."""
import argparse
import base64
from functools import lru_cache
import grp
import json
import os
from pathlib import Path, PurePosixPath
import socketserver
import stat
import sys
from urllib.parse import unquote, urlsplit

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from portal.node_transport import read_message, encode_message, socket_call

SOCKET = '/run/gpu-platform/worker.sock'


@lru_cache(maxsize=2)
def kube_session(modified):
    cfg = yaml.safe_load(Path('/etc/rancher/k3s/k3s.yaml').read_text())
    cluster = cfg['clusters'][0]['cluster']
    user = cfg['users'][0]['user']
    directory = Path('/run/gpu-platform/worker-credentials')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = []
    for filename, value in [('ca.crt', cluster['certificate-authority-data']),
                            ('client.crt', user['client-certificate-data']), ('client.key', user['client-key-data'])]:
        target = directory / filename
        target.write_bytes(base64.b64decode(value))
        target.chmod(0o600)
        files.append(str(target))
    session = requests.Session()
    session.trust_env = False
    session.verify = files[0]
    session.cert = tuple(files[1:])
    return session


def kube_request(method, path, body=None, params=None, text=False):
    parsed = urlsplit(path)
    if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'} or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('Invalid Kubernetes request')
    if not path.startswith(('/api/v1/', '/apis/')) or '..' in unquote(path).split('/'):
        raise ValueError('Invalid Kubernetes API path')
    session = kube_session(Path('/etc/rancher/k3s/k3s.yaml').stat().st_mtime_ns)
    headers = {'Content-Type': 'application/merge-patch+json'} if method == 'PATCH' else {}
    response = session.request(method, 'https://127.0.0.1:6443' + path, json=body, params=params,
                               headers=headers, timeout=(5, 30))
    return {'status': response.status_code, 'data': response.text if text or not response.ok else response.json() if response.content else None}


def dataset_request(cfg, action='list', path='', query='', offset=0):
    relative = PurePosixPath(path)
    if relative.is_absolute() or '..' in relative.parts or '\x00' in path:
        raise ValueError('Invalid dataset path')
    descriptor = os.open(cfg['storage']['datasets'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts:
            following = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        metadata = os.fstat(descriptor)
        if action == 'read':
            if not stat.S_ISREG(metadata.st_mode) or not isinstance(offset, int) or offset < 0:
                raise ValueError('Invalid dataset read')
            return {'data': base64.b64encode(os.pread(descriptor, 256 * 1024, offset)).decode(), 'size': metadata.st_size}
        if action != 'list':
            raise ValueError('Invalid dataset operation')
        if stat.S_ISREG(metadata.st_mode):
            return {'file': True, 'size': metadata.st_size}
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError('Invalid dataset entry')
        entries = []
        with os.scandir(descriptor) as directory:
            for entry in directory:
                if query.casefold() not in entry.name.casefold():
                    continue
                info = entry.stat(follow_symlinks=False)
                kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'link'
                entries.append({'name': entry.name, 'kind': kind, 'size': info.st_size, 'modified': info.st_mtime,
                                'path': str(relative / entry.name), 'container_path': '/datasets/' + str(relative / entry.name)})
                if len(entries) > 10000:
                    raise ValueError('Directory too large; use a more specific path')
        return {'entries': sorted(entries, key=lambda item: (item['kind'] != 'directory', item['name'].casefold()))}
    finally:
        os.close(descriptor)


def dispatch(cfg, operation, parameters):
    if operation == 'kube':
        return kube_request(**parameters)
    if operation == 'observe':
        pools = []
        for pool in cfg['storage']['pools']:
            data = os.statvfs(pool['path'])
            pools.append({**pool, 'capacity_gib': data.f_blocks * data.f_frsize // 2**30,
                          'free_gib': data.f_bavail * data.f_frsize // 2**30})
        return {'protocol': 1, 'config': {key: cfg[key] for key in ('platform', 'network', 'storage', 'registry')}, 'pools': pools}
    if operation == 'volume_stats':
        import subprocess
        output = subprocess.check_output(['k3s', 'kubectl', 'get', '--raw',
                                         '/api/v1/nodes/' + parameters['node'] + '/proxy/stats/summary'], text=True, timeout=30)
        return json.loads(output)
    if operation == 'registry':
        path = parameters['path']
        if not path.startswith('/v2/') or '..' in unquote(path).split('/') or urlsplit(path).netloc:
            raise ValueError('Invalid registry path')
        if parameters['method'] not in {'GET', 'DELETE'}:
            raise ValueError('Invalid registry method')
        session = requests.Session()
        session.trust_env = False
        identity = parameters['identity']
        session.auth = identity['login'], identity['password']
        address = cfg['network']['registry_service_ip'] + ':5000'
        response = session.request(parameters['method'], 'https://' + address + path,
                                   headers=parameters.get('headers'), verify='/etc/rancher/k3s/platform-registry-ca.crt', timeout=30)
        return {'status': response.status_code, 'data': response.text, 'headers': dict(response.headers)}
    if operation == 'dataset':
        return dataset_request(cfg, **parameters)
    raise ValueError('Unsupported worker operation')


def serve(cfg):
    class Handler(socketserver.StreamRequestHandler):
        timeout = 50
        def handle(self):
            try:
                message = read_message(self.rfile)
                result = {'ok': True, 'result': dispatch(cfg, message['operation'], message.get('parameters', {}))}
            except Exception as exc:
                result = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:500]}
            self.wfile.write(encode_message(result))
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    path = Path(SOCKET)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with Server(SOCKET, Handler) as server:
        os.chown(SOCKET, 0, grp.getgrnam('gpu-platform').gr_gid)
        os.chmod(SOCKET, 0o660)
        server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['serve', 'call'])
    parser.add_argument('--config', type=Path, default=Path('/etc/gpu-platform/config.yaml'))
    args = parser.parse_args()
    if args.mode == 'call':
        try:
            message = read_message(sys.stdin.buffer)
            result = socket_call(SOCKET, message)
            sys.stdout.buffer.write(encode_message({'ok': True, 'result': result}))
        except Exception as exc:
            sys.stdout.buffer.write(encode_message({'ok': False, 'error': str(exc)[:500]}))
    else:
        serve(yaml.safe_load(args.config.read_text()))
