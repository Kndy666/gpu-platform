#!/usr/bin/env python3
"""Real student Docker workflow; always remove the resources created by this test."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import uuid
import requests
from workspace_cleanup import cleanup_account

from environment import enabled, gpu_inventory, require_features, require_commands, deployment_config, kubectl, portal_url

ROOT = Path(__file__).resolve().parents[2]


def main():
    require_features('cluster', 'docker', 'gpu')
    cfg = deployment_config()
    ns = cfg['platform']['namespace']
    work_ns = cfg['platform']['workload_namespace']
    url = portal_url()
    username = 'docker-check-'+uuid.uuid4().hex[:10]
    credentials = {}
    logs_by_task = {}

    def app(code, payload=None):
        result = subprocess.run([*kubectl(), '-n', ns, 'exec', '-i', 'deployment/platform', '--',
                                 'python', 'manage.py', 'shell', '-c', code],
                                input=json.dumps(payload or {}), text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def api(token, method, path, data=None):
        response = requests.request(method, url+'/api/v1'+path, json=data,
                                    headers={'Authorization': 'Bearer '+token}, timeout=30)
        if not response.ok:
            raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: {response.text[:1000]}')
        return response.json()['data']

    def wait_task(pk, statuses, timeout=300):
        deadline = time.monotonic()+timeout
        previous = None
        while time.monotonic() < deadline:
            record = api(credentials['student'], 'GET', '/tasks/'+pk)
            if record['status'] == 'running' and record['mode'] == 'batch':
                logs = api(credentials['student'], 'GET', '/tasks/'+pk+'/logs')
                if logs['text']:
                    logs_by_task[pk] = logs['text']
            if previous != record['status']:
                print('TASK', pk, record['status'], flush=True)
                previous = record['status']
            if record['status'] in statuses:
                if pk in logs_by_task:
                    print(logs_by_task[pk][-3000:], flush=True)
                return record
            if record['status'] in {'failed', 'expired', 'rejected', 'canceled'}:
                raise RuntimeError(str({k: record[k] for k in ['id', 'status', 'error', 'exit_code']})+'\n'+logs_by_task.get(pk,''))
            time.sleep(2)
        raise TimeoutError('Task did not reach '+str(statuses))

    require_commands('ssh', 'ssh-keygen')
    gpu_inventory(app)
    try:
        with tempfile.TemporaryDirectory(prefix='gpu-platform-docker-e2e-') as directory:
            directory = Path(directory)
            key = directory/'identity'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
            credentials = app('''import json,sys,secrets
from django.contrib.auth import get_user_model
from django.db import transaction
from portal.api_auth import issue_token
from portal.models import GROUP_QUOTA_FIELDS
from portal.models import ImageArtifact,SSHKey
from portal.services import parse_ssh_key
data=json.load(sys.stdin)
with transaction.atomic():
 image=ImageArtifact.objects.filter(shared=True,verified=True,state='ready',node__transport='local').latest('created_at')
 user=get_user_model().objects.create_user(data['username'],password=secrets.token_urlsafe(24))
 user.profile.home_quota_gib=1
 user.profile.resource_limits={'unlimited': True, **{field: None for field in GROUP_QUOTA_FIELDS}}
 user.profile.approval_mode='manual'
 user.profile.save()
 public,fingerprint=parse_ssh_key(data['public_key'])
 SSHKey.objects.create(user=user,name='Docker verification',public_key=public,fingerprint=fingerprint)
 admin=get_user_model().objects.filter(is_staff=True,is_superuser=True,is_active=True).first()
 print(json.dumps({'user_id':user.pk,'student':issue_token(user,1800,name=data['username'])['token'],
  'admin':issue_token(admin,1800,name=data['username']+'-admin')['token'],'image':str(image.pk),'reference':image.reference+'@'+image.digest}))''',
                              {'username': username, 'public_key': key.with_suffix('.pub').read_text()})
            print('TEST_ACCOUNT', username, credentials['user_id'], flush=True)
            now = datetime.now(timezone.utc)
            source = api(credentials['student'], 'POST', '/tasks', {
                'mode': 'interactive', 'purpose': 'Docker 验证环境', 'image': credentials['image'],
                'gpu': 0, 'cpu': 2, 'memory_gib': 4, 'workspace_gib': 10,
                'starts_at': (now-timedelta(minutes=1)).isoformat(),
                'ends_at': (now+timedelta(hours=1)).isoformat(), 'working_dir': '/workspace'})['id']
            api(credentials['admin'], 'POST', '/tasks/'+source+'/review', {'action': 'approve', 'priority': 0})
            ready = wait_task(source, {'running'})
            connection = ready['connection']
            ssh = ['ssh', '-i', str(key), '-p', str(connection['port']), '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile='+str(directory/'known_hosts'),
                   '-o', 'HostKeyAlias='+connection['alias'], connection['user']+'@'+connection['host']]
            cuda_source = (ROOT/'tests/e2e/cuda_check.c').read_text()
            script = '''set -euo pipefail
test "$(whoami)" = USERNAME
test "$HOME" = /home/USERNAME
docker info --format '{{json .SecurityOptions}}' | grep -q rootless
docker compose version
docker buildx version
docker pull IMAGE_REFERENCE
docker build -t local-check - <<'DOCKERFILE'
FROM IMAGE_REFERENCE
USER 0
RUN printf image-preserved > /usr/local/share/docker-check
DOCKERFILE
docker volume create check-state
NETWORK_CHECK
docker run --rm --user 0 --entrypoint /bin/bash -v check-state:/state local-check -c 'echo volume-preserved > /state/marker'
cat > /workspace/compose-check.yaml <<'COMPOSE'
services:
  check:
    image: local-check
    user: '0'
    entrypoint: ['/bin/bash', '-c', 'set -e; test "$$(cat /usr/local/share/docker-check)" = image-preserved; echo compose-ok']
COMPOSE
docker compose -p platform-check -f /workspace/compose-check.yaml up --abort-on-container-exit --exit-code-from check
docker compose -p platform-check -f /workspace/compose-check.yaml down --volumes
docker run --rm -d --name port-check --user 0 -p 127.0.0.1:18080:8080 --entrypoint /bin/bash local-check -c 'while true; do printf "HTTP/1.1 200 OK\\r\\nContent-Length: 7\\r\\nConnection: close\\r\\n\\r\\nport-ok" | nc -l -p 8080 -q 1; done'
for attempt in $(seq 1 15); do curl -fsS http://127.0.0.1:18080 > /workspace/port-result && break; sleep 1; done
test "$(cat /workspace/port-result)" = port-ok
docker stop --timeout 5 port-check
if docker run --rm --gpus all --entrypoint /bin/true local-check >/workspace/no-gpu.log 2>&1; then exit 77; fi
sudo -n sh -c 'printf outer-preserved > /usr/local/share/platform-docker-check'
cat > /workspace/cuda-check.c <<'CUDA_SOURCE'
CUDA_PROGRAM
CUDA_SOURCE
gcc /workspace/cuda-check.c -ldl -o /workspace/cuda-check
docker tag local-check REGISTRY_HOST/user-USER_ID/docker-e2e:denied
if timeout 60 docker push REGISTRY_HOST/user-USER_ID/docker-e2e:denied > /workspace/ro-push.log 2>&1; then exit 78; fi
grep -Ei 'denied|unauthorized' /workspace/ro-push.log
printf 'DOCKER_PREPARATION_PASS\\n'
'''.replace('USERNAME', username).replace('IMAGE_REFERENCE', credentials['reference'])
            network_check = "docker run --rm --user 0 --entrypoint /bin/bash local-check -c 'apt-get update'" if enabled('network') else 'true'
            script = script.replace('NETWORK_CHECK', network_check)
            script = script.replace('CUDA_PROGRAM', cuda_source).replace('USER_ID', str(credentials['user_id'])).replace('REGISTRY_HOST', cfg['registry']['host'])
            result = subprocess.run(ssh+['/bin/bash -s'], input=script, text=True, capture_output=True, timeout=300)
            print(result.stdout[-5000:], result.stderr[-3000:], flush=True)
            if result.returncode:
                raise RuntimeError('Docker preparation failed: '+str(result.returncode))
            command = ("test \"$(cat /usr/local/share/platform-docker-check)\" = outer-preserved && "
                       "docker run --rm --user 0 --gpus all -v /workspace:/workspace -v check-state:/state "
                       "--entrypoint /bin/bash local-check -c " + shlex.quote(
                       'test "$(cat /usr/local/share/docker-check)" = image-preserved && '
                       'test "$(cat /state/marker)" = volume-preserved && /workspace/cuda-check')+
                       '; result=$?; sleep 12; exit "$result"')
            target = api(credentials['student'], 'POST', '/tasks', {'mode': 'batch',
                'source_kind': 'environment', 'source_environment': source, 'purpose': 'Docker GPU 验证',
                'gpu': 1, 'gpu_requests': [{'compute_percent':100,'memory_gib':1}], 'cpu': 2, 'memory_gib': 4, 'working_dir': '/workspace', 'command': command})['id']
            api(credentials['admin'], 'POST', '/tasks/'+target+'/review', {'action': 'approve', 'priority': 0})
            completed = wait_task(target, {'succeeded'}, 360)
            assert completed['exit_code'] == 0, completed
            state = app('''import json,sys
from portal.models import ResourceRequest,Workspace,EnvironmentSnapshot
from portal.services import work_volume
data=json.load(sys.stdin);source=ResourceRequest.objects.get(pk=data['source']);target=ResourceRequest.objects.get(pk=data['target'])
snapshot=EnvironmentSnapshot.objects.get(target=target);ws=Workspace.objects.get(request=target)
print(json.dumps({'same_workspace':work_volume(source).pk==work_volume(target).pk,
 'source_stopped':source.status=='canceled','snapshot_saved':snapshot.status=='succeeded','gpu_count':len(ws.gpu_devices)}))''',
                        {'source': source, 'target': target})
            assert state == {'same_workspace': True, 'source_stopped': True, 'snapshot_saved': True, 'gpu_count': 1}, state
            print('PASS: student SSH, Rootless Docker build, Compose, port publishing, read-only registry, GPU handoff and retained Docker volume', flush=True)
    finally:
        cleanup_account(app,cfg,username,credentials,'docker-check-',docker_probe=True)


if __name__ == '__main__':
    main()
