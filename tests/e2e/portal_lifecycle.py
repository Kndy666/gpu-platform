#!/usr/bin/env python3
"""Live portal -> approval -> Kueue -> SSH/PVC -> completion acceptance."""
import argparse
import fcntl
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import time
import uuid
from urllib.parse import urlsplit
import requests

ROOT = Path(__file__).resolve().parents[2]

def kube(*args, data=None):
    return subprocess.run(['k3s', 'kubectl', *args], input=data, text=True, stdout=subprocess.PIPE, check=True).stdout

class Browser:
    def __init__(self, base, username, password):
        self.base = base.rstrip('/')
        self.session = requests.Session(); self.session.trust_env = False
        self.session.get(self.base+'/accounts/login/', timeout=15).raise_for_status()
        response = self.post('/accounts/login/', {'username': username, 'password': password}, redirect=False)
        if response.status_code != 302: raise RuntimeError('Portal login failed: '+username)

    def get(self, path): return self.session.get(self.base+path, timeout=20)
    def post(self, path, data, redirect=True):
        data = dict(data); data['csrfmiddlewaretoken'] = self.session.cookies.get('csrftoken', '')
        return self.session.post(self.base+path, data=data, timeout=30, allow_redirects=redirect)

class Acceptance:
    def __init__(self, base, directory):
        self.base = base; self.directory = directory; self.directory.mkdir(parents=True, exist_ok=True); self.directory.chmod(0o700)
        previous = self.directory/'report.json'
        self.record = json.loads(previous.read_text()) if previous.exists() else {'tests': [], 'tasks': []}
        self.users = []

    def passed(self, name, **details):
        self.record['tests'].append({'name': name, 'status': 'pass', **details})
        self.write(); print(name, 'PASS', flush=True)

    def write(self):
        with (self.directory/'report.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.directory/'report.json'
            current = json.loads(path.read_text()) if path.exists() else {'tests': [], 'tasks': []}
            tests = {v['name']: v for v in current['tests']}
            tests.update({v['name']: v for v in self.record['tests']})
            tasks = {v['id']: v for v in current['tasks']}
            tasks.update({v['id']: v for v in self.record['tasks']})
            self.record = {'tests': list(tests.values()), 'tasks': list(tasks.values())}
            temporary = self.directory/('report.'+str(os.getpid())+'.tmp')
            temporary.write_text(json.dumps(self.record, indent=2, ensure_ascii=False)); temporary.replace(path)

    def prepare(self):
        state = self.directory/'credentials.json'
        if not state.exists():
            suffix = uuid.uuid4().hex[:6]
            users = []
            for name in ['alice', 'bob', 'carol', 'dave', 'eve']:
                username = f'acceptance-{name}-{suffix}'
                key = self.directory/username
                subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key], check=True)
                users.append({'username': username, 'password': secrets.token_urlsafe(24), 'key': str(key)})
            payload = {'users': users}
            state.write_text(json.dumps(payload)); state.chmod(0o600)
        payload = json.loads(state.read_text())
        raw = kube('-n','gpu-platform','exec','deploy/platform','--','python','manage.py','shell','-c','import json;from portal.models import ImageArtifact;print(json.dumps({"image":ImageArtifact.objects.filter(shared=True,verified=True).order_by("-created_at").values_list("id",flat=True).first()},default=str))')
        payload['image'] = json.loads(raw.strip().splitlines()[-1])['image']
        if payload['image'] is None: raise RuntimeError('Prepare the production shared base image first')
        raw = kube('-n', 'gpu-platform', 'exec', '-i', 'deploy/platform', '--', 'python', 'manage.py', 'shell', '-c', (ROOT/'tests/e2e/seed_fixture.py').read_text(), data=json.dumps(payload))
        result = json.loads(raw.strip().splitlines()[-1])
        payload['image'] = result['image']; state.write_text(json.dumps(payload)); state.chmod(0o600)
        self.image = payload['image']
        password = base64.b64decode(kube('-n', 'gpu-platform', 'get', 'secret', 'platform-secrets', '-o', 'jsonpath={.data.initial-admin-password}')).decode()
        self.admin = Browser(self.base, 'admin', password)
        for item in payload['users']:
            item['id'] = next(v['id'] for v in result['users'] if v['username'] == item['username'])
            item['browser'] = Browser(self.base, item['username'], item['password'])
            keypage = item['browser'].get('/keys/')
            if item['username'] not in keypage.text or 'acceptance-key' not in keypage.text:
                response = item['browser'].post('/keys/', {'name': 'acceptance-key', 'public_key': Path(item['key']+'.pub').read_text()})
                response.raise_for_status()
                if 'acceptance-key' not in response.text: raise RuntimeError('Public key upload failed')
            self.users.append(item)
        self.passed('five_web_accounts_and_distinct_ssh_keys')

    def create(self, user, purpose, mode='interactive', workspace=1, argv=None, working_dir='/workspace', gpu=1):
        existing = next((t for t in self.record['tasks'] if t['purpose'] == purpose and t['username'] == user['username']), None)
        if existing:
            return {'id': existing['id'], 'path': '/tasks/'+existing['id']+'/', 'user': user, 'purpose': purpose}
        data = {'image': self.image, 'gpu': gpu, 'cpu': 8, 'memory_gib': 32, 'workspace_gib': workspace,
                'purpose': purpose, 'source_kind': 'image', 'starts_at': (datetime.now(ZoneInfo('Asia/Shanghai'))-timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M') if mode == 'interactive' else '',
                'ends_at': (datetime.now(ZoneInfo('Asia/Shanghai'))+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M') if mode == 'interactive' else '',
                'command': shlex.join(argv or []), 'working_dir': working_dir}
        result = user['browser'].post('/environments/new/' if mode == 'interactive' else '/training/new/', data, redirect=False)
        if result.status_code != 302: raise RuntimeError('Submit failed: '+re.sub('<[^>]+>', ' ', result.text)[-2000:])
        path = urlsplit(result.headers['Location']).path
        task = {'id': path.strip('/').split('/')[-1], 'path': path, 'user': user, 'purpose': purpose}
        response = self.admin.post(path+'review/', {'action': 'approve', 'priority': '0', 'reason': '端到端验收'})
        response.raise_for_status()
        status = self.status(task)
        if status['status'] == 'submitted': raise RuntimeError('Approval failed: '+re.sub('<[^>]+>', ' ', response.text)[-2000:])
        self.record['tasks'].append({'id': task['id'], 'purpose': purpose, 'username': user['username']})
        self.write()
        return task

    def status(self, task):
        deadline = time.monotonic()+60
        while True:
            try:
                response = task['user']['browser'].get(task['path']+'status/')
                if response.status_code not in {502, 503, 504}:
                    response.raise_for_status(); return response.json()
            except requests.ConnectionError:
                if time.monotonic() >= deadline: raise
            if time.monotonic() >= deadline: raise RuntimeError('Portal unavailable after deployment')
            time.sleep(2)

    def wait(self, task, states, timeout=240):
        deadline = time.monotonic()+timeout
        last = None
        while time.monotonic() < deadline:
            last = self.status(task)
            if last['status'] in states: return last
            if last['status'] in {'failed', 'rejected', 'canceled', 'expired'} and last['status'] not in states: raise RuntimeError(str(last))
            time.sleep(2)
        raise TimeoutError(str(last))

    def ssh(self, task, command, data=None, check=True, key_user=None, timeout=30):
        state = self.status(task)
        args = ['ssh', '-i', (key_user or task['user'])['key'], '-p', str(state['ssh_port']), '-o', 'BatchMode=yes',
                '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile='+str(self.directory/'known_hosts'),
                '-o', 'HostKeyAlias=task-'+task['id'],
                state['login_name']+'@'+urlsplit(self.base).hostname, command]
        result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout)
        if check and result.returncode: raise RuntimeError(result.stderr[-2000:])
        return result

    def stop(self, task):
        task['user']['browser'].post(task['path']+'stop/', {}).raise_for_status()
        return self.wait(task, {'canceled', 'succeeded', 'failed', 'expired', 'cleaned'})

    def run(self):
        a, b = self.users[:2]
        first = self.create(a, '验收 · 独立 GPU 工作环境', workspace=100)
        self.wait(first, {'running'})
        gpu = self.ssh(first, 'nvidia-smi --query-gpu=uuid --format=csv,noheader').stdout.strip().splitlines()
        assert len(gpu) == 1
        state = self.status(first)
        local = self.directory/'scp-marker'
        local.write_text('scp-sftp-marker')
        options = ['-i', a['key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
                   '-o', 'UserKnownHostsFile='+str(self.directory/'known_hosts'), '-o', 'HostKeyAlias=task-'+first['id']]
        host = state['login_name']+'@'+urlsplit(self.base).hostname
        subprocess.run(['scp', '-q', '-P', str(state['ssh_port']), *options, str(local), host+':/workspace/scp-marker'], check=True, timeout=30)
        downloaded = self.directory/'sftp-marker'
        subprocess.run(['sftp', '-q', '-P', str(state['ssh_port']), *options, '-b', '-', host],
                       input=f'get /workspace/scp-marker {downloaded}\n', text=True, check=True, timeout=30)
        assert downloaded.read_text() == local.read_text()
        self.ssh(first, "printf 'permanent-marker' > $HOME/permanent-marker; printf 'first-workspace' > /workspace/first-marker")
        assert b['browser'].get(first['path']).status_code == 404
        assert self.ssh(first, 'true', check=False, key_user=b).returncode != 0
        self.passed('one_gpu_8cpu_32gib_100gib_and_cross_user_isolation')
        second = self.create(a, '验收 · 用户并发队列')
        self.wait(second, {'queued'})
        time.sleep(12)
        assert self.status(first)['status'] == 'running' and self.status(second)['status'] == 'queued'
        self.stop(first); self.wait(second, {'running'})
        assert self.ssh(second, 'cat $HOME/permanent-marker').stdout == 'permanent-marker'
        self.ssh(second, 'test ! -e /workspace/first-marker')
        self.passed('native_per_user_concurrency_and_permanent_volume')
        self.stop(second)
        tasks = [self.create(user, '验收 · 四卡队列 '+str(i)) for i, user in enumerate(self.users)]
        deadline = time.monotonic()+240
        while time.monotonic() < deadline:
            states = [self.status(t) for t in tasks]
            if sum(s['status'] == 'running' for s in states) == 4 and sum(s['status'] == 'queued' for s in states) == 1: break
            time.sleep(2)
        else: raise RuntimeError('Four-running/one-queued not reached: '+str(states))
        running = [t for t, s in zip(tasks, states) if s['status'] == 'running']
        waiting = next(t for t, s in zip(tasks, states) if s['status'] == 'queued')
        uuids = [self.ssh(t, 'nvidia-smi --query-gpu=uuid --format=csv,noheader').stdout.strip() for t in running]
        assert len(set(uuids)) == 4
        self.stop(running[0]); self.wait(waiting, {'running'})
        self.passed('four_unique_gpus_fifth_waits_and_auto_admits')
        for task in tasks:
            if self.status(task)['status'] not in {'canceled', 'succeeded', 'failed'}: self.stop(task)
        script = '#!/bin/bash\necho training-start\nsleep 30\necho training-end\n'
        helper = self.create(a, '验收 · 配置命令任务')
        self.wait(helper, {'running'})
        self.ssh(helper, 'cat > "$HOME/train.sh"', data=script)
        self.stop(helper)
        batch = self.create(a, '验收 · 自动运行程序', mode='batch', argv=['bash', '/home/'+a['username']+'/train.sh'])
        self.wait(batch, {'running'})
        final = self.wait(batch, {'succeeded'})
        assert final['exit_code'] == 0 and final['ssh_port'] is None
        self.passed('real_batch_shell_ssh_and_exit_zero')
        failed = self.create(a, '验收 · 保留退出码42', mode='batch', argv=['bash', '-lc', 'sleep 8; exit 42'])
        final = self.wait(failed, {'failed'})
        assert final['exit_code'] == 42
        canceled = self.create(a, '验收 · 人工取消命令任务', mode='batch', argv=['sleep', '600'])
        self.wait(canceled, {'running'}); final = self.stop(canceled)
        assert final['status'] == 'canceled'
        self.passed('real_batch_exit_42_no_retry_and_cancellation')

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--base', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args(); test = Acceptance(args.base, args.directory)
    try: test.prepare(); test.run()
    finally: test.write()

if __name__ == '__main__': main()
