#!/usr/bin/env python3
"""Live API/SSH sharing checks. Remove only this run's generated account/resources."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import uuid
import requests
import yaml
import sys
from workspace_cleanup import cleanup_account

ROOT=Path(__file__).resolve().parents[2]

def main():
    if os.geteuid()!=0:raise SystemExit('Run with sudo')
    cfg=yaml.safe_load((ROOT/'config.yaml').read_text())
    ns=cfg['platform']['namespace'];work_ns=cfg['platform']['workload_namespace']
    url=('https://' if cfg['network']['https'] else 'http://')+cfg['platform']['public_host']
    username='sharing-check-'+uuid.uuid4().hex[:8]
    credentials={};tasks=[]
    def app(code,payload=None):
        r=subprocess.run(['k3s','kubectl','-n',ns,'exec','-i','deployment/platform','--','python','manage.py','shell','-c',code],
            input=json.dumps(payload or {}),text=True,capture_output=True)
        if r.returncode:raise RuntimeError(r.stderr[-4000:])
        return json.loads(r.stdout.strip().splitlines()[-1])
    def api(token,method,path,data=None):
        r=requests.request(method,url+'/api/v1'+path,json=data,headers={'Authorization':'Bearer '+token},timeout=30)
        if not r.ok:raise RuntimeError(f'{method} {path}: {r.status_code}: {r.text[:1000]}')
        return r.json()['data']
    def wait_task(pk,statuses,timeout=240):
        deadline=time.monotonic()+timeout;previous=None
        while time.monotonic()<deadline:
            r=api(credentials['student'],'GET','/tasks/'+pk)
            if previous!=r['status']:print('TASK',pk,r['status'],flush=True);previous=r['status']
            if r['status'] in statuses:return r
            if r['status'] in {'failed','expired','rejected','canceled'}:
                print('ERROR',r['error'],flush=True);raise RuntimeError(str(r))
            time.sleep(2)
        raise TimeoutError(str(r))
    def create(count,percent,memory=3,gpu_requests=None):
        now=datetime.now(timezone.utc)
        r=api(credentials['student'],'POST','/tasks',{'purpose':f'GPU {count}x{percent}% check','mode':'interactive',
            'image':credentials['image'],'gpu':count,'gpu_requests':gpu_requests if gpu_requests is not None else [{'compute_percent':percent,'memory_gib':memory} for _ in range(count)],
            'cpu':2,'memory_gib':4,'workspace_gib':2,'starts_at':(now-timedelta(minutes=1)).isoformat(),
            'ends_at':(now+timedelta(hours=1)).isoformat()})
        tasks.append(r['id']);api(credentials['admin'],'POST','/tasks/'+r['id']+'/review',{'action':'approve','priority':0})
        return wait_task(r['id'],{'running'})
    def stop(r):
        api(credentials['student'],'POST','/tasks/'+r['id']+'/stop',{})
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if app('import json,sys;from portal.models import Workspace;print(json.dumps(Workspace.objects.get(request_id=json.load(sys.stdin)["id"]).cleanup_complete))',{'id':r['id']}):return
            time.sleep(2)
        raise TimeoutError('GPU cleanup pending')
    try:
        with tempfile.TemporaryDirectory(prefix='gpu-sharing-e2e-') as directory:
            directory=Path(directory);key=directory/'identity'
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            credentials=app('''import json,sys,secrets
from django.contrib.auth import get_user_model
from django.db import transaction
from portal.models import ImageArtifact,SSHKey,UserGroup
from portal.api_auth import issue_token
from portal.services import parse_ssh_key
d=json.load(sys.stdin)
with transaction.atomic():
 g=UserGroup.objects.create(name=d['username'],gpu_compute_percent=400,gpu_memory_gib=64,cpu=24,memory_gib=48,workspace_gib=32,home_gib=1,image_gib=10,concurrent_tasks=4,interactive_hours=2)
 u=get_user_model().objects.create_user(d['username'],password=secrets.token_urlsafe(24));u.profile.group=g;u.profile.home_quota_gib=1;u.profile.save()
 p,f=parse_ssh_key(d['public_key']);SSHKey.objects.create(user=u,name='verification',public_key=p,fingerprint=f)
 i=ImageArtifact.objects.get(shared=True,name='Ubuntu 22.04',state='ready');a=get_user_model().objects.get(username='admin')
 print(json.dumps({'student':issue_token(u,3600,name=d['username'])['token'],'admin':issue_token(a,3600,name=d['username'])['token'],
  'user_id':u.pk,'group':g.pk,'image':str(i.pk),'reference':i.reference+'@'+i.digest}))''',{'username':username,'public_key':key.with_suffix('.pub').read_text()})
            print('TEST_ACCOUNT',username,flush=True)
            if '--program-only' in sys.argv:
                command="set -e\ncat > /workspace/quota.c <<'SOURCE'\n"+(ROOT/'tests/e2e/gpu_quota.c').read_text()+"\nSOURCE\ngcc /workspace/quota.c -ldl -o /workspace/quota\n/workspace/quota 2 28 3072 64 0\n"
                command+="if sudo -n nvidia-smi memory-limits -i 0 -n /sys/fs/cgroup --soft-limit 0 --hard-limit max; then exit 9; else echo QUOTA_CHANGE_DENIED; fi\nsleep 12\n"
                r=api(credentials['student'],'POST','/tasks',{'purpose':'shared multi-GPU program check','mode':'batch',
                    'image':credentials['image'],'gpu':2,'gpu_requests':[{'compute_percent':25,'memory_gib':3} for _ in range(2)],'cpu':2,'memory_gib':4,'workspace_gib':2,'command':command})
                tasks.append(r['id']);api(credentials['admin'],'POST','/tasks/'+r['id']+'/review',{'action':'approve','priority':0})
                ready=wait_task(r['id'],{'running','succeeded'})
                logs='';deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    record=api(credentials['student'],'GET','/tasks/'+r['id'])
                    current=api(credentials['student'],'GET','/tasks/'+r['id']+'/logs')['text']
                    if current:logs=current
                    if record['status']=='succeeded':break
                    if record['status']=='failed':raise RuntimeError(record['error'])
                    time.sleep(1)
                else:raise TimeoutError('Program did not finish')
                print(logs[-4000:],flush=True)
                assert record['exit_code']==0 and 'QUOTA_CHANGE_DENIED' in logs,record
                print('PASS: shared two-GPU batch program, Kueue Job admission and privileged quota-change rejection',flush=True)
                return
            def ssh(r,script,check=True,timeout=120):
                c=r['connection'];command=['ssh','-i',str(key),'-p',str(c['port']),'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new',
                    '-o','UserKnownHostsFile='+str(directory/'known_hosts'),'-o','HostKeyAlias='+c['alias'],c['user']+'@'+c['host'],'/bin/bash -s']
                result=subprocess.run(command,input=script,text=True,capture_output=True,timeout=timeout)
                print(result.stdout[-3000:],result.stderr[-1000:],flush=True)
                if check and result.returncode:raise RuntimeError('SSH check failed: '+str(result.returncode))
                return result
            probe=(ROOT/'tests/e2e/gpu_quota.c').read_text()
            def setup(r):
                ssh(r,"set -e\ncat > /workspace/quota.c <<'SOURCE'\n"+probe+"\nSOURCE\ngcc /workspace/quota.c -ldl -o /workspace/quota\n")
            if '--mixed-only' in sys.argv:
                holder=create(1,25,2);setup(holder)
                ssh(holder,'set -e\nnohup /workspace/quota 1 28 2048 0 600 > /workspace/hold.log 2>&1 < /dev/null &\nsleep 1\ngrep READY /workspace/hold.log\n')
                specs=[{'compute_percent':100,'memory_gib':5},{'compute_percent':50,'memory_gib':3}]
                mixed=create(2,100,gpu_requests=specs);setup(mixed)
                assert mixed['gpu_requests']==specs and mixed['gpu_compute_total']==150 and mixed['gpu_memory_total_gib']==8
                allocations=mixed['gpu_allocations']
                assert allocations[1]['uuid']==holder['gpu_allocations'][0]['uuid'] and allocations[0]['uuid']!=allocations[1]['uuid'],allocations
                ssh(mixed,'/workspace/quota 2 114,56 5120,3072 64 0\n')
                image=shlex.quote(credentials['reference'])
                ssh(mixed,'set -e\ndocker pull '+image+'\ndocker run --rm --user 0 --gpus all -v /workspace:/workspace --entrypoint /workspace/quota '+image+' 2 114,56 5120,3072 64 0\n',timeout=180)
                timeline=api(credentials['student'],'GET','/timeline')
                blocks=[x for x in timeline['blocks'] if x.get('id')==mixed['id'] and 'gpu_slot' in x]
                assert [(x['gpu_slot'],x['compute_percent'],x['memory_gib']) for x in blocks]==[(0,100,5),(1,50,3)],blocks
                stop(mixed);ssh(holder,'/workspace/quota 1 28 2048 0 0\n');stop(holder)
                print('PASS: full + half GPU, per-card VRAM, CUDA order in outer/inner Docker, timeline and release',flush=True)
                return
            a=create(1,25);setup(a)
            ssh(a,'set -e\n/workspace/quota 1 28 3072 0 0\nnohup /workspace/quota 1 28 3072 2048 600 > /workspace/hold.log 2>&1 < /dev/null &\nsleep 2\ngrep READY /workspace/hold.log\n')
            b=create(2,50,4);setup(b)
            assert len({v['uuid'] for v in b['gpu_allocations']})==2,b['gpu_allocations']
            assert a['gpu_allocations'][0]['uuid'] in {v['uuid'] for v in b['gpu_allocations']}
            ssh(b,'set -e\n/workspace/quota 2 56 4096 0 0\nnohup /workspace/quota 2 56 4096 256 600 > /workspace/hold.log 2>&1 < /dev/null &\nsleep 2\ngrep READY /workspace/hold.log\n')
            image=shlex.quote(credentials['reference'])
            inner='docker run --rm --user 0 --gpus all -v /workspace:/workspace --entrypoint /workspace/quota '+image
            ssh(a,'set -e\ndocker pull '+image+'\n'+inner+' 1 28 3072 0 0\nif '+inner+' 1 28 3072 1536 0; then exit 9; fi\ngrep READY /workspace/hold.log\npkill -f "^/workspace/quota 1 28 3072 2048"\n'+inner+' 1 28 3072 2048 0\n',timeout=180)
            ssh(a,'set -e\nCUDA_MPS_SM_PARTITION=invalid /workspace/quota 1 28 3072 0 0\nif CUDA_MPS_PIPE_DIRECTORY=/tmp/no-mps /workspace/quota 1 28 3072 0 0; then exit 8; fi\n')
            ssh(b,'set -e\ndocker pull '+image+'\n'+inner+' 2 56 4096 512 0\n')
            timeline=api(credentials['student'],'GET','/timeline')
            assert any(x.get('compute_percent')==50 for x in timeline['blocks']),timeline
            stop(a);c=create(1,35);setup(c);ssh(c,'/workspace/quota 1 38 3072 0 0\n')
            ssh(b,'set -e\ngrep READY /workspace/hold.log\n/workspace/quota 2 56 4096 0 0\n')
            stop(c);stop(b)
            for count,percent,sms in [(2,100,114),(4,50,56),(4,100,114)]:
                r=create(count,percent);setup(r)
                ssh(r,f'/workspace/quota {count} {sms} 3072 128 0\n')
                # Optional NCCL test runtime is supplied outside the default image.
                nccl=os.environ.get('PLATFORM_NCCL_TEST_DIR')
                if nccl:
                    source=Path(nccl);payload=subprocess.run(['tar','-C',str(source),'-czf','-','.'],capture_output=True,check=True).stdout
                    conn=r['connection'];scp=['ssh','-i',str(key),'-p',str(conn['port']),'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(directory/'known_hosts'),'-o','HostKeyAlias='+conn['alias'],conn['user']+'@'+conn['host'],'mkdir -p /workspace/nccl; tar -C /workspace/nccl -xzf -']
                    subprocess.run(scp,input=payload,check=True,timeout=120)
                    ssh(r,f'cd /workspace/nccl && gcc check.c -ldl -o check && LD_LIBRARY_PATH=$PWD ./check {count}\n',timeout=120)
                stop(r)
            print('PASS: API, per-task SM/VRAM, shared live allocation/release, inner Docker, 2/4 physical GPUs',flush=True)
    finally:
        cleanup_account(app,cfg,username,credentials,'sharing-check-')

if __name__=='__main__':main()
