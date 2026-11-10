#!/usr/bin/env python3
"""Generate actual cluster quotas from Node Allocatable after vendor installation."""
import argparse
import base64
import datetime
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import yaml
import tempfile

def kubectl(*args, data=None):
    p = subprocess.run(['k3s', 'kubectl', *args], input=data, text=True, stdout=subprocess.PIPE, check=True)
    return p.stdout

def apply(obj): kubectl('apply', '-f', '-', data=yaml.safe_dump(obj))

def secret(namespace, name, strings):
    existing = kubectl('-n', namespace, 'get', 'secret', name, '--ignore-not-found', '-o', 'json')
    if existing:
        obj = json.loads(existing)
        missing = {k: base64.b64encode(v.encode()).decode() for k, v in strings.items() if k not in obj.get('data', {})}
        if missing:
            obj.setdefault('data', {}).update(missing)
            kubectl('replace', '-f', '-', data=yaml.safe_dump(obj))
        return obj
    obj = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'namespace': namespace}, 'stringData': strings}
    apply(obj)

def registry(cfg):
    p = cfg['platform']; ns = p['namespace']; host = cfg['registry']['host'].split(':')[0]
    existing = kubectl('-n', ns, 'get', 'secret', 'registry-tls', '--ignore-not-found', '-o', 'json')
    if not existing:
        with tempfile.TemporaryDirectory() as d:
            crt, key = Path(d)/'cert.pem', Path(d)/'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '3650',
                '-keyout', str(key), '-out', str(crt), '-subj', '/CN='+host,
                '-addext', 'subjectAltName=DNS:'+host+',IP:'+cfg['network']['registry_service_ip']], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            secret(ns, 'registry-tls', {'tls.crt': crt.read_text(), 'tls.key': key.read_text()})
        existing = kubectl('-n', ns, 'get', 'secret', 'registry-tls', '-o', 'json')
    cert = base64.b64decode(json.loads(existing)['data']['tls.crt']).decode()
    initial = {'distSpecVersion': '1.1.1', 'storage': {'rootDirectory': '/var/lib/registry', 'gc': True},
        'http': {'address': '0.0.0.0', 'port': '5000', 'tls': {'cert': '/etc/zot/tls/tls.crt', 'key': '/etc/zot/tls/tls.key'},
            'auth': {'htpasswd': {'path': '/etc/zot/auth/htpasswd'}}, 'accessControl': {'repositories': {'**': {'defaultPolicy': []}}}}, 'log': {'level': 'info'}}
    secret(ns, 'registry-state', {'users.json': '{}', 'htpasswd': '', 'config.json': json.dumps(initial)})
    for target in [ns, p['build_namespace'], p['workload_namespace']]:
        apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'registry-ca', 'namespace': target}, 'data': {'tls.crt': cert}})
    buildkit_conf = f'[registry."{cfg["registry"]["host"]}"]\n  ca = ["/registry-ca/tls.crt"]\n'
    for source, endpoints in cfg['network'].get('registry_mirrors', {}).items():
        if source == cfg['registry']['host']: continue
        addresses = [value.removeprefix('https://').rstrip('/') for value in endpoints]
        buildkit_conf += f'[registry.{json.dumps(source)}]\n  mirrors = {json.dumps(addresses)}\n'
    apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'buildkit-config', 'namespace': p['build_namespace']}, 'data': {'buildkitd.toml': buildkit_conf}})
    # Containerd resolves the internal registry via its stable Service IP and verifies its CA.
    registry_cfg = Path('/etc/rancher/k3s/registries.yaml')
    content = yaml.safe_load(registry_cfg.read_text()) if registry_cfg.exists() else {}
    ca_path = Path('/etc/rancher/k3s/platform-registry-ca.crt')
    changed = not ca_path.exists() or ca_path.read_text() != cert
    ca_path.write_text(cert); ca_path.chmod(0o644)
    desired_endpoint = f'https://{cfg["network"]["registry_service_ip"]}:5000'
    mirrors = content.setdefault('mirrors', {})
    old = mirrors.get(cfg['registry']['host'])
    desired = {'endpoint': [desired_endpoint]}
    if old and old != desired: raise RuntimeError('Existing registry endpoint differs; reconcile manually')
    mirrors[cfg['registry']['host']] = desired
    content.setdefault('configs', {})[cfg['network']['registry_service_ip']+':5000'] = {'tls': {'ca_file': str(ca_path)}}
    if not registry_cfg.exists() or yaml.safe_load(registry_cfg.read_text()) != content:
        registry_cfg.write_text(yaml.safe_dump(content)); changed = True
    if changed: subprocess.run(['systemctl', 'restart', 'k3s'], check=True)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(); cfg = yaml.safe_load(args.config.read_text()); p = cfg['platform']
    nodes = json.loads(kubectl('get', 'nodes', '-o', 'json'))['items']
    ready = [n for n in nodes if any(c['type'] == 'Ready' and c['status'] == 'True' for c in n['status']['conditions'])]
    if len(ready) != 1: raise RuntimeError('This installation supports exactly one Ready node')
    node = ready[0]; alloc = node['status']['allocatable']
    flavor = {'apiVersion': 'kueue.x-k8s.io/v1beta1', 'kind': 'ResourceFlavor', 'metadata': {'name': 'platform-node'},
              'spec': {'nodeLabels': {'kubernetes.io/hostname': node['metadata']['labels']['kubernetes.io/hostname']}}}
    apply(flavor)
    resources = [{'name': key, 'nominalQuota': alloc.get(key, '0')} for key in ['cpu', 'memory', 'gpu.platform/compute', 'gpu.platform/memory-mib', 'ephemeral-storage']]
    apply({'apiVersion': 'kueue.x-k8s.io/v1beta1', 'kind': 'ClusterQueue', 'metadata': {'name': p['queue'], 'labels': {'platform.queue': 'pool'}},
           'spec': {'cohort': p['queue'], 'namespaceSelector': {'matchLabels': {'platform.queue': 'enabled'}}, 'queueingStrategy': 'BestEffortFIFO',
                    'preemption': {'withinClusterQueue': 'Never'},
                    'resourceGroups': [{'coveredResources': ['cpu', 'memory', 'gpu.platform/compute', 'gpu.platform/memory-mib', 'ephemeral-storage'], 'flavors': [{'name': 'platform-node', 'resources': resources}]}]}})
    apply({'apiVersion': 'kueue.x-k8s.io/v1beta1', 'kind': 'LocalQueue', 'metadata': {'name': p['queue'], 'namespace': p['workload_namespace']}, 'spec': {'clusterQueue': p['queue']}})
    for name, value in [('platform-normal', 0), ('platform-high', 100)]:
        apply({'apiVersion': 'kueue.x-k8s.io/v1beta1', 'kind': 'WorkloadPriorityClass', 'metadata': {'name': name}, 'value': value})
    if p.get('role', 'controller') == 'controller':
        secret(p['namespace'], 'platform-secrets', {'django-secret-key': secrets.token_urlsafe(64), 'initial-admin-password': secrets.token_urlsafe(24)})
    registry(cfg)
    host = node['metadata']['labels']['kubernetes.io/hostname']
    apply({'apiVersion': 'v1', 'kind': 'PersistentVolume', 'metadata': {'name': 'platform-public-datasets'},
           'spec': {'capacity': {'storage': '1Pi'}, 'accessModes': ['ReadOnlyMany'], 'persistentVolumeReclaimPolicy': 'Retain',
                    'storageClassName': '', 'local': {'path': cfg['storage']['datasets']},
                    'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [host]}]}]}}}})
    apply({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'public-datasets', 'namespace': p['workload_namespace']},
           'spec': {'accessModes': ['ReadOnlyMany'], 'storageClassName': '', 'volumeName': 'platform-public-datasets', 'resources': {'requests': {'storage': '1Gi'}}}})
    print('Kueue queues, priorities, initial application secret and read-only datasets configured.')

if __name__ == '__main__': main()
