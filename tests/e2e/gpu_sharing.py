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
import sys
from workspace_cleanup import cleanup_account

from environment import gpu_inventory, quota_arguments, enabled, require_features, require_commands, deployment_config, kubectl, portal_url

ROOT=Path(__file__).resolve().parents[2]


def main():
    require_features('cluster', 'gpu')
    cfg=deployment_config()
    ns=cfg['platform']['namespace'];work_ns=cfg['platform']['workload_namespace']
    url=portal_url()
    username='sharing-check-'+uuid.uuid4().hex[:8]
    credentials={};tasks=[]
    def app(code,payload=None):
        r=subprocess.run([*kubectl(),'-n',ns,'exec','-i','deployment/platform','--','python','manage.py','shell','-c',code],
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
    require_commands('ssh', 'ssh-keygen')
    minimum_gpus = 2 if '--mixed-only' in sys.argv else 1
    memory_gib = 3 if '--program-only' in sys.argv else 5 if '--mixed-only' in sys.argv else 7
    devices = gpu_inventory(app, minimum=minimum_gpus, memory_gib=memory_gib)
    try:
        with tempfile.TemporaryDirectory(prefix='gpu-sharing-e2e-') as directory:
            directory=Path(directory);key=directory/'identity'
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            credentials=app('''import json,sys,secrets
from django.contrib.auth import get_user_model
from django.db import transaction
from portal.models import ImageArtifact,SSHKey,UserGroup
from portal.api_auth import issue_token
from portal.models import GROUP_QUOTA_FIELDS
from portal.services import parse_ssh_key
d=json.load(sys.stdin)
with transaction.atomic():
 g=UserGroup.objects.create(name=d['username'],gpu_compute_percent=400,gpu_memory_gib=64,cpu=24,memory_gib=48,workspace_gib=32,home_gib=1,image_gib=10,concurrent_tasks=4,interactive_hours=2)
 u=get_user_model().objects.create_user(d['username'],password=secrets.token_urlsafe(24))
 u.profile.group=g
 u.profile.home_quota_gib=1
 u.profile.resource_limits={'unlimited': True, **{field: None for field in GROUP_QUOTA_FIELDS}}
 u.profile.approval_mode='manual'
 u.profile.save()
 p,f=parse_ssh_key(d['public_key']);SSHKey.objects.create(user=u,name='verification',public_key=p,fingerprint=f)
 i=ImageArtifact.objects.filter(shared=True,verified=True,node__transport='local',state='ready').latest('created_at');a=get_user_model().objects.filter(is_staff=True,is_superuser=True,is_active=True).first()
 print(json.dumps({'student':issue_token(u,3600,name=d['username'])['token'],'admin':issue_token(a,3600,name=d['username'])['token'],
  'user_id':u.pk,'group':g.pk,'image':str(i.pk),'reference':i.reference+'@'+i.digest}))''',{'username':username,'public_key':key.with_suffix('.pub').read_text()})
            print('TEST_ACCOUNT',username,flush=True)
            if '--program-only' in sys.argv:
                command="set -e\ncat > /workspace/check.c <<'SOURCE'\n"+(ROOT/'tests/e2e/cuda_check.c').read_text()+"\nSOURCE\ngcc /workspace/check.c -ldl -o /workspace/check\n/workspace/check\n"
                command+="if sudo -n nvidia-smi memory-limits -i 0 -n /sys/fs/cgroup --soft-limit 0 --hard-limit max; then exit 9; else echo QUOTA_CHANGE_DENIED; fi\nsleep 12\n"
                r=api(credentials['student'],'POST','/tasks',{'purpose':'shared GPU program check','mode':'batch',
                    'image':credentials['image'],'gpu':1,'gpu_requests':[{'compute_percent':25,'memory_gib':3}],'cpu':2,'memory_gib':4,'workspace_gib':2,'command':command})
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
                print('PASS: shared GPU batch program, Kueue Job admission and privileged quota-change rejection',flush=True)
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
            def quota(record):
                return quota_arguments(record, devices)
            if '--mixed-only' in sys.argv:
                holder=create(1,25,2);setup(holder)
                ssh(holder,'set -e\nnohup /workspace/quota '+quota(holder)+' 0 600 > /workspace/hold.log 2>&1 < /dev/null &\nsleep 1\ngrep READY /workspace/hold.log\n')
                specs=[{'compute_percent':100,'memory_gib':5},{'compute_percent':50,'memory_gib':3}]
                mixed=create(2,100,gpu_requests=specs);setup(mixed)
                assert mixed['gpu_requests']==specs and mixed['gpu_compute_total']==150 and mixed['gpu_memory_total_gib']==8
                allocations=mixed['gpu_allocations']
                assert allocations[1]['uuid']==holder['gpu_allocations'][0]['uuid'] and allocations[0]['uuid']!=allocations[1]['uuid'],allocations
                ssh(mixed,'/workspace/quota '+quota(mixed)+' 64 0\n')
                if enabled('docker'):
                    image=shlex.quote(credentials['reference'])
                    ssh(mixed,'set -e\ndocker pull '+image+'\ndocker run --rm --user 0 --gpus all -v /workspace:/workspace --entrypoint /workspace/quota '+image+' '+quota(mixed)+' 64 0\n',timeout=180)
                timeline=api(credentials['student'],'GET','/timeline')
                blocks=[x for x in timeline['blocks'] if x.get('id')==mixed['id'] and 'gpu_slot' in x]
                assert [(x['gpu_slot'],x['compute_percent'],x['memory_gib']) for x in blocks]==[(0,100,5),(1,50,3)],blocks
                stop(mixed);ssh(holder,'/workspace/quota '+quota(holder)+' 0 0\n');stop(holder)
                print(json.dumps({'status':'passed','mixed_quotas':True,'docker':'passed' if enabled('docker') else 'skipped'}),flush=True)
                return
            a=create(1,25);setup(a)
            ssh(a,'set -e\n/workspace/quota '+quota(a)+' 0 0\nnohup /workspace/quota '+quota(a)+' 2048 600 > /workspace/hold.log 2>&1 < /dev/null &\nsleep 2\ngrep READY /workspace/hold.log\n')
            b=create(min(2, len(devices)),50,4);setup(b)
            assert len({v['uuid'] for v in b['gpu_allocations']})==min(2, len(devices)),b['gpu_allocations']
            assert a['gpu_allocations'][0]['uuid'] in {v['uuid'] for v in b['gpu_allocations']}
            ssh(b,'set -e\n/workspace/quota '+quota(b)+' 0 0\nnohup /workspace/quota '+quota(b)+' 256 600 > /workspace/hold.log 2>&1 < /dev/null &\nsleep 2\ngrep READY /workspace/hold.log\n')
            if enabled('docker'):
                image=shlex.quote(credentials['reference'])
                inner='docker run --rm --user 0 --gpus all -v /workspace:/workspace --entrypoint /workspace/quota '+image
                ssh(a,'set -e\ndocker pull '+image+'\n'+inner+' '+quota(a)+' 0 0\nif '+inner+' '+quota(a)+' 1536 0; then exit 9; fi\ngrep READY /workspace/hold.log\npkill -f "^/workspace/quota '+quota(a)+' 2048"\n'+inner+' '+quota(a)+' 2048 0\n',timeout=180)
                ssh(b,'set -e\ndocker pull '+image+'\n'+inner+' '+quota(b)+' 512 0\n')
            else:
                ssh(a,'pkill -f "^/workspace/quota .* 2048"\n')
            ssh(a,'set -e\nCUDA_MPS_SM_PARTITION=invalid /workspace/quota '+quota(a)+' 0 0\nif CUDA_MPS_PIPE_DIRECTORY=/tmp/no-mps /workspace/quota '+quota(a)+' 0 0; then exit 8; fi\n')
            timeline=api(credentials['student'],'GET','/timeline')
            assert any(x.get('compute_percent')==50 for x in timeline['blocks']),timeline
            stop(a);c=create(1,35);setup(c);ssh(c,'/workspace/quota '+quota(c)+' 0 0\n')
            ssh(b,'set -e\ngrep READY /workspace/hold.log\n/workspace/quota '+quota(b)+' 0 0\n')
            stop(c);stop(b)
            for count,percent in [(1,100),(2,100),(4,50),(4,100)]:
                if count > len(devices):
                    print(json.dumps({'status':'skipped','reason':f'{count}-GPU scenario requires {count} devices'}),flush=True)
                    continue
                r=create(count,percent);setup(r)
                ssh(r,f'/workspace/quota {quota(r)} 128 0\n')
                # Optional NCCL test runtime is supplied outside the default image.
                nccl=os.environ.get('PLATFORM_NCCL_TEST_DIR')
                if enabled('nccl'):
                    if not nccl:
                        raise ValueError('PLATFORM_NCCL_TEST_DIR is required when nccl is enabled')
                    source=Path(nccl);payload=subprocess.run(['tar','-C',str(source),'-czf','-','.'],capture_output=True,check=True).stdout
                    conn=r['connection'];scp=['ssh','-i',str(key),'-p',str(conn['port']),'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(directory/'known_hosts'),'-o','HostKeyAlias='+conn['alias'],conn['user']+'@'+conn['host'],'mkdir -p /workspace/nccl; tar -C /workspace/nccl -xzf -']
                    subprocess.run(scp,input=payload,check=True,timeout=120)
                    ssh(r,f'cd /workspace/nccl && gcc check.c -ldl -o check && LD_LIBRARY_PATH=$PWD PLATFORM_CUDART_LIBRARY={shlex.quote(os.environ.get("PLATFORM_CUDART_LIBRARY", "libcudart.so"))} ./check {count}\n',timeout=120)
                stop(r)
            print(json.dumps({'status':'passed','devices':len(devices),'sharing':True,
                              'docker':'passed' if enabled('docker') else 'skipped',
                              'nccl':'passed' if enabled('nccl') else 'skipped'}),flush=True)
    finally:
        cleanup_account(app,cfg,username,credentials,'sharing-check-')

if __name__=='__main__':main()
