#!/usr/bin/env python3
"""Real CSI ENOSPC/re-attach test for all configured pools. Uses only named test PVCs."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
import yaml

from environment import require_features, config_path, kubectl as kubectl_command

def kubectl(*args, obj=None):
    result = subprocess.run([*kubectl_command(), *args], input=yaml.safe_dump(obj) if obj else None,
                             text=True, stdout=subprocess.PIPE, check=True)
    return result.stdout

WRITER = '''import errno,json,os,time
root='/volume'
with open(root+'/marker','w') as marker: marker.write('persistent-marker')
block=b'p'*(8*1024*1024)
started=time.monotonic()
with open(root+'/performance','wb',buffering=0) as f:
 for _ in range(8):f.write(block)
 os.fsync(f.fileno())
write_seconds=time.monotonic()-started
started=time.monotonic()
with open(root+'/performance','rb',buffering=0) as f:
 while f.read(8*1024*1024):pass
read_seconds=time.monotonic()-started
os.unlink(root+'/performance')
while not os.path.exists(root+'/continue'):time.sleep(.1)
block=b'x'*(8*1024*1024)
written=0
try:
 with open(root+'/fill','wb',buffering=0) as output:
  while written < 2*1024**3:
   output.write(block); written+=len(block)
except OSError as e:
 assert e.errno==errno.ENOSPC, repr(e)
 print(json.dumps({'enospc':True,'written_bytes':written,'write_64mib_seconds':round(write_seconds,4),'cached_read_64mib_seconds':round(read_seconds,4)}),flush=True)
else:
 raise AssertionError('2GiB write succeeded on a 1GiB PVC')
os.unlink(root+'/fill')
'''

def pod(name, pvc, image, command):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'labels': {'platform.test': 'quota'}},
            'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'terminationGracePeriodSeconds': 2,
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000, 'fsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [{'name': 'test', 'image': image, 'command': ['python', '-c', command],
                    'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                    'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
                    'volumeMounts': [{'name': 'volume', 'mountPath': '/volume'}]}], 'volumes': [{'name': 'volume', 'persistentVolumeClaim': {'claimName': pvc}}]}}

def wait(namespace, name):
    deadline = time.monotonic()+300
    while time.monotonic() < deadline:
        obj = json.loads(kubectl('-n', namespace, 'get', 'pod', name, '-o', 'json'))
        phase = obj.get('status', {}).get('phase')
        if phase in {'Succeeded', 'Failed'}:
            logs = kubectl('-n', namespace, 'logs', name)
            if phase != 'Succeeded': raise RuntimeError(logs)
            return logs
        time.sleep(2)
    raise TimeoutError(name)


def main():
    require_features('cluster')
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=config_path())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); cfg = yaml.safe_load(args.config.read_text())
    ns = 'platform-storage-test-'+uuid.uuid4().hex[:8]
    kubectl('create', 'namespace', ns)
    report = []
    try:
        for pool in cfg['storage']['pools']:
            name = 'quota-'+pool['name']
            pvc = {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': name},
                   'spec': {'storageClassName': pool['storage_class'], 'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '1Gi'}}}}
            kubectl('-n', ns, 'apply', '-f', '-', obj=pvc)
            kubectl('-n', ns, 'apply', '-f', '-', obj=pod(name, name, cfg['platform']['app_image'], WRITER))
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                state=json.loads(kubectl('-n',ns,'get','pod',name,'-o','json'))
                if state.get('status',{}).get('phase')=='Running':break
                time.sleep(1)
            else:raise TimeoutError('Initial volume mount')
            claim=json.loads(kubectl('-n',ns,'get','pvc',name,'-o','json'))
            backing=pool['path']+'/'+claim['spec']['volumeName']+'/disk.img'
            size,blocks=map(int,kubectl('-n','openebs','exec','daemonset/rawfile-rawfile-localpv-node','-c','csi-driver','--','stat','-c','%s %b',backing).split())
            assert blocks*512>=size,(size,blocks)
            kubectl('-n',ns,'exec',name,'--','touch','/volume/continue')
            result = json.loads(wait(ns, name).strip().splitlines()[-1])
            result['initial_reserved_bytes']=blocks*512
            kubectl('-n', ns, 'delete', 'pod', name, '--wait=true')
            check = "from pathlib import Path; assert Path('/volume/marker').read_text()=='persistent-marker'; print('REATTACH PASS')"
            kubectl('-n', ns, 'apply', '-f', '-', obj=pod(name+'-restore', name, cfg['platform']['app_image'], check))
            assert 'REATTACH PASS' in wait(ns, name+'-restore')
            report.append({'pool': pool['name'], 'hard_quota': result, 'reattach': 'pass'})
            print(pool['name'], 'ENOSPC + reattach PASS', flush=True)
    finally:
        # Namespace was generated exclusively for this test; only its scratch PVCs are removed.
        kubectl('delete', 'namespace', ns, '--wait=true', '--timeout=180s')
        args.output.write_text(json.dumps(report, indent=2))

if __name__ == '__main__': main()
