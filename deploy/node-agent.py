#!/usr/bin/env python3
"""Trusted local worker: GPU inventory and typed environment-save operations."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import urllib.request
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGE_CLEANUP_DIR = Path('/var/lib/gpu-platform/image-cleanup')

def run(argv, **kwargs):
    return subprocess.check_output([str(v) for v in argv], text=True, timeout=kwargs.pop('timeout', 60), **kwargs)

def kube(*args, data=None):
    return run(['k3s', 'kubectl', *args], input=data)

def annotate(resource, ns, name, key, value):
    args = ['annotate', resource, name, key+'='+json.dumps(value, separators=(',', ':')), '--overwrite']
    if ns: args = ['-n', ns, *args]
    kube(*args)

def cleanup_local_images(record):
    references = json.loads(record.read_text())
    complete = True
    for reference in references:
        try:
            run(['k3s', 'ctr', '--namespace', 'k8s.io', 'images', 'rm', reference], stderr=subprocess.PIPE)
        except (subprocess.SubprocessError, OSError) as exc:
            complete = False
            print(f'Local image cleanup pending: {reference} ({type(exc).__name__})', flush=True)
    if complete:
        record.unlink(missing_ok=True)

def retry_image_cleanup():
    for record in IMAGE_CLEANUP_DIR.glob('*.json'):
        try:
            cleanup_local_images(record)
        except (OSError, ValueError) as exc:
            print(f'Local image cleanup record: {record.name} ({type(exc).__name__})', flush=True)

@contextmanager
def temporary_snapshot_images(operation, image, destination):
    # Persist before commit so interrupted operations can release their exact
    # local references on restart. Registry tags and container snapshots stay intact.
    IMAGE_CLEANUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = IMAGE_CLEANUP_DIR / (operation.hex + '.json')
    pending = record.with_suffix('.tmp')
    pending.write_text(json.dumps([destination, image]))
    pending.chmod(0o600)
    pending.replace(record)
    try:
        yield
    finally:
        try:
            cleanup_local_images(record)
        except (OSError, ValueError) as exc:
            # Preserve the original save result; the next worker tick retries.
            print(f'Local image cleanup deferred: {operation} ({type(exc).__name__})', flush=True)

def observe(cfg):
    from gpu_runtime import GPUManager
    GPUManager(cfg).tick()

def save_environment(cfg, obj):
    ns = cfg['platform']['namespace']; work_ns = cfg['platform']['workload_namespace']
    request = json.loads(obj['data']['request'])
    operation = uuid.UUID(request['id']); source = uuid.UUID(request['source']); owner = int(request['owner'])
    if obj['metadata']['name'] != 'snapshot-'+operation.hex: raise RuntimeError('Operation identity mismatch')
    pods = json.loads(kube('-n', work_ns, 'get', 'pods', '-l', 'platform.task='+str(source), '-o', 'json'))['items']
    if len(pods) != 1: raise RuntimeError('源环境已停止，请重新启动环境。')
    pod = pods[0]
    if pod['metadata']['labels'].get('platform.user') != str(owner) or pod.get('status', {}).get('phase') != 'Running':
        raise RuntimeError('源环境不可用。')
    status = next(c for c in pod['status']['containerStatuses'] if c['name'] == 'workspace')
    container = status['containerID'].removeprefix('containerd://')
    if not re.fullmatch('[a-f0-9]{64}', container): raise RuntimeError('Invalid container ID')
    command = [ROOT/'.tools/nerdctl', '--address', '/run/k3s/containerd/containerd.sock', '--namespace', 'k8s.io']
    changes = run([*command, 'diff', container])
    excluded = [v['mountPath'].rstrip('/') for c in pod['spec']['containers'] for v in c.get('volumeMounts', [])]
    for line in changes.splitlines():
        path = line[2:].strip()
        if any(path == p or path.startswith(p+'/') for p in excluded): continue
        if re.search(r'(^|/)(\.ssh|\.aws|\.kube|\.env|credentials|id_rsa|id_ed25519)(/|$)|\.(pem|key)$', path):
            raise RuntimeError('可写层包含凭据文件，请移至个人卷后重试。')
    host = cfg['registry']['host']; repo = f'user-{owner}/environment'; destination = f'{host}/{repo}:{operation.hex}'
    image = 'docker.io/library/platform-save:'+operation.hex
    secret = json.loads(kube('-n', work_ns, 'get', 'secret', f'registry-u{owner}', '-o', 'json'))
    credentials = json.loads(base64.b64decode(secret['data']['.dockerconfigjson']))
    credentials['auths'][cfg['network']['registry_service_ip']+':5000'] = credentials['auths'][host]
    address = 'https://'+cfg['network']['registry_service_ip']+':5000'
    ca = '/etc/rancher/k3s/platform-registry-ca.crt'
    with temporary_snapshot_images(operation, image, destination):
        # Runtime identity and GPU leases belong to this execution, not its image.
        changes=[]
        for name,value in {'NVIDIA_VISIBLE_DEVICES':'void','CUDA_VISIBLE_DEVICES':'','CUDA_MPS_PIPE_DIRECTORY':'',
            'PLATFORM_GPU_TASK':'','PLATFORM_GPU_DEVICES':'','PLATFORM_GPU_LEASE':''}.items():
            changes.extend(['--change',f'ENV {name}={value}'])
        run([*command, 'commit', '--pause=true', '--format', 'oci', *changes, container, image], timeout=300)
        with tempfile.TemporaryDirectory(prefix='gpu-platform-publish-') as directory:
            directory = Path(directory); auth = directory/'auth'; auth.mkdir(mode=0o700)
            (auth/'config.json').write_text(json.dumps(credentials)); (auth/'config.json').chmod(0o600)
            hosts = directory/'certs.d'/host; hosts.mkdir(parents=True)
            (hosts/'hosts.toml').write_text(f'server = "{address}"\n[host."{address}"]\n  capabilities = ["pull", "resolve", "push"]\n  ca = "{ca}"\n')
            env = os.environ.copy(); env['DOCKER_CONFIG'] = str(auth)
            run(['k3s', 'ctr', 'images', 'convert', '--platform', 'linux/'+cfg['platform']['architecture'], '--oci', image, destination], timeout=300)
            run([*command, '--hosts-dir', directory/'certs.d', 'push', '--quiet', '--platform', 'linux/'+cfg['platform']['architecture'], destination], env=env, timeout=600)
            identity = credentials['auths'][host]['auth']
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
            def manifest(ref):
                req = urllib.request.Request(address+'/v2/'+repo+'/manifests/'+ref, headers={'Authorization': 'Basic '+identity, 'Accept': 'application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json'})
                with opener.open(req, timeout=30) as response:
                    raw = response.read(); doc = json.loads(raw); digest = response.headers.get('Docker-Content-Digest', 'sha256:'+hashlib.sha256(raw).hexdigest())
                if 'manifests' in doc:
                    return manifest(next(m['digest'] for m in doc['manifests'] if m.get('platform', {}).get('architecture') == cfg['platform']['architecture']))
                return {'reference': destination, 'digest': digest, 'size_bytes': sum(x['size'] for x in doc['layers'])+doc['config']['size']}
            result = manifest(operation.hex)
        return result

def tick(cfg):
    retry_image_cleanup()
    observe(cfg)
    ns = cfg['platform']['namespace']
    operations = json.loads(kube('-n', ns, 'get', 'configmaps', '-l', 'platform.operation=snapshot', '-o', 'json'))['items']
    for obj in operations:
        if obj['data'].get('status') != 'pending': continue
        obj['data']['status'] = 'working'
        kube('replace', '-f', '-', data=json.dumps(obj))
        try:
            result = save_environment(cfg, obj)
            patch = {'data': {'status': 'succeeded', 'result': json.dumps(result)}}
        except subprocess.CalledProcessError as exc:
            print('Environment operation failed: '+str(exc), flush=True)
            patch = {'data': {'status': 'failed', 'error': '环境镜像保存失败，请重试或联系管理员。'}}
        except Exception as exc:
            patch = {'data': {'status': 'failed', 'error': str(exc)[:1200]}}
        kube('-n', ns, 'patch', 'configmap', obj['metadata']['name'], '--type=merge', '-p', json.dumps(patch))

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, required=True); parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    try:
        ns = cfg['platform']['namespace']
        for operation in json.loads(kube('-n', ns, 'get', 'configmaps', '-l', 'platform.operation=snapshot', '-o', 'json'))['items']:
            if operation['data'].get('status') != 'working': continue
            request = json.loads(operation['data']['request'])
            source = uuid.UUID(request['source'])
            for pod in json.loads(kube('-n', cfg['platform']['workload_namespace'], 'get', 'pods', '-l', 'platform.task='+str(source), '-o', 'json'))['items']:
                if pod['metadata']['labels'].get('platform.user') != str(int(request['owner'])): continue
                for c in pod.get('status', {}).get('containerStatuses', []):
                    if c['name'] != 'workspace' or not c.get('containerID'): continue
                    try: run([ROOT/'.tools/nerdctl', '--address', '/run/k3s/containerd/containerd.sock', '--namespace', 'k8s.io', 'unpause', c['containerID'].removeprefix('containerd://')], stderr=subprocess.DEVNULL)
                    except subprocess.CalledProcessError: pass
            kube('-n',ns,'patch','configmap',operation['metadata']['name'],'--type=merge','-p',json.dumps({'data':{'status':'failed','error':'保存操作中断，开发环境已保留，请重新提交。'}}))
    except Exception as exc: print('Recovery: '+type(exc).__name__,flush=True)
    while True:
        try: tick(yaml.safe_load(args.config.read_text()))
        except Exception as exc: print(type(exc).__name__+': '+str(exc)[:300], flush=True)
        if args.once: break
        time.sleep(10)

if __name__ == '__main__': main()
