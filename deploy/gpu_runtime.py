#!/usr/bin/env python3
"""Host-owned GPU leases, MPS namespaces and the OCI prestart quota hook."""
from contextlib import contextmanager
import csv
import ctypes
import fcntl
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from portal.gpu_math import COMPUTE, MEMORY, GATE, choose_devices,validate_gpu_requests

STATE = Path('/var/lib/gpu-platform/gpu')
PIPES = STATE/'pipes'
RECORDS = STATE/'leases'
SERVER = 'shared'
log=logging.getLogger(__name__)

def run(args, **kw):
    return subprocess.check_output([str(x) for x in args], text=True, timeout=kw.pop('timeout',30), **kw)

@contextmanager
def locked():
    STATE.mkdir(mode=0o711, parents=True, exist_ok=True)
    RECORDS.mkdir(mode=0o700, exist_ok=True)
    with (STATE/'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield

def save(record):
    name=RECORDS/(record['task']+'.json')
    temporary=name.with_suffix('.tmp')
    temporary.write_text(json.dumps(record));temporary.chmod(0o600);temporary.replace(name)

def mps(*args):
    return run(['nvidia-cuda-mps-control','-p','3',*args], env={**os.environ,
        'CUDA_MPS_PIPE_DIRECTORY':str(PIPES),'CUDA_MPS_LOG_DIRECTORY':str(STATE/'logs')},stderr=subprocess.STDOUT)

def cuda_inventory():
    lib=ctypes.CDLL('libcuda.so.1')
    if lib.cuInit(0):raise RuntimeError('CUDA inventory unavailable')
    devices=[]
    for row in csv.reader(run(['nvidia-smi','--query-gpu=uuid,index,name,memory.total,pci.bus_id','--format=csv,noheader,nounits']).splitlines()):
        index=int(row[1]);sms=ctypes.c_int()
        if lib.cuDeviceGetAttribute(ctypes.byref(sms),16,index):raise RuntimeError('Cannot query GPU SM count')
        devices.append({'uuid':row[0].strip(),'index':index,'name':row[2].strip(),'memory_mib':int(row[3]),
            'usable_memory_mib':max(0,int(row[3])-512),'pci':row[4].strip(),'sm_count':sms.value,'ready':True})
    return devices

def set_memory(gpu, cgroup, amount):
    run(['nvidia-smi','memory-limits','-i',gpu,'-n',cgroup,'--soft-limit',str(amount),
         '--hard-limit',str(amount) if amount else 'max'],stderr=subprocess.STDOUT)

def quota_hook():
    state=json.load(sys.stdin)
    spec=json.loads((Path(state['bundle'])/'config.json').read_text())
    env=dict(s.split('=',1) for s in spec.get('process',{}).get('env',[]) if '=' in s)
    if not env.get('PLATFORM_GPU_TASK'):return
    task=str(uuid.UUID(env['PLATFORM_GPU_TASK']))
    with locked():
        record=json.loads((RECORDS/(task+'.json')).read_text())
        if not secrets.compare_digest(record['token'],env.get('PLATFORM_GPU_LEASE','')):raise RuntimeError('GPU lease identity mismatch')
        path=Path('/proc',str(int(state['pid'])),'cgroup').read_text().strip().split('0::')[1]
        expected=record['pod_uid'].replace('-','_')
        if expected not in path or not path.startswith('/kubepods.slice/'):raise RuntimeError('GPU cgroup identity mismatch')
        cgroup='/sys/fs/cgroup'+path
        # Toolkit replaces NVIDIA_VISIBLE_DEVICES with "void" after CDI device
        # injection. Validate the immutable Pod declaration through our own key.
        if env.get('PLATFORM_GPU_DEVICES')!=','.join(a['uuid'] for a in record['allocations']):raise RuntimeError('GPU device selection mismatch')
        applied=[]
        try:
            for a in record['allocations']:
                set_memory(a['uuid'],cgroup,a['memory_mib']);applied.append(a['uuid'])
        except Exception:
            for gpu in applied:set_memory(gpu,cgroup,0)
            raise
        record['cgroup']=cgroup;record['container_id']=state['id'];save(record)

class GPUManager:
    def __init__(self,cfg):self.cfg=cfg;self.ns=cfg['platform']['workload_namespace']
    def kube(self,*args,data=None):return run(['k3s','kubectl',*args],input=data)
    def records(self):return [json.loads(p.read_text()) for p in sorted(RECORDS.glob('*.json'))]
    def ready(self,devices):
        PIPES.mkdir(mode=0o711,parents=True,exist_ok=True)
        (STATE/'logs').mkdir(mode=0o700,exist_ok=True)
        try:listing=mps('server','list','--format=csv,noheader')
        except subprocess.CalledProcessError:
            if self.records():raise RuntimeError('GPU 服务中断，原任务无法继续运行')
            for d in devices:run(['nvidia-smi','-i',d['uuid'],'-c','EXCLUSIVE_PROCESS'])
            run(['systemctl','start','gpu-platform-mps.service'])
            mps('context-share','set','default_socket=off')
            listing=''
        if not any(row and row[0].strip()==SERVER and 'Ready' in row for row in csv.reader(listing.splitlines())):
            if self.records():raise RuntimeError('GPU 服务中断，原任务无法继续运行')
            for d in devices:run(['nvidia-smi','-i',d['uuid'],'-c','EXCLUSIVE_PROCESS'])
            mps('context-share','set','default_socket=off')
            mps('server','create',SERVER,'--uid=0','--allowed-devices='+','.join(d['uuid'] for d in devices))
            time.sleep(1)
            if 'Ready' not in mps('server','list',SERVER):raise RuntimeError('GPU 服务启动中')
    def annotate(self,pod,key,value):
        self.kube('-n',self.ns,'annotate','pod',pod['metadata']['name'],key+'='+value,'--overwrite')
    def release(self,record):
        name='t_'+uuid.UUID(record['task']).hex
        try:
            namespaces=list(csv.reader(mps('namespace','list','--server='+SERVER,'--format=csv,noheader').splitlines()))
            if any(name in [v.strip() for v in row] for row in namespaces):mps('namespace','delete',name,'--server='+SERVER,'--force')
            cgroup=record.get('cgroup')
            if cgroup and Path(cgroup).exists():
                for a in record['allocations']:set_memory(a['uuid'],cgroup,0)
            partitions=list(csv.reader(mps('sm-partition','list','--server='+SERVER,'--all','--format=csv,noheader').splitlines()))
            for a in record['allocations']:
                if any(a['partition'] in [v.strip() for v in row] for row in partitions):mps('sm-partition','delete',a['partition'],'--server='+SERVER,'--device='+a['uuid'])
        except subprocess.CalledProcessError:
            # A restarted controller can be alive without the old shared server.
            # Keep its lease until the container/cgroup has disappeared.
            if record.get('cgroup') and Path(record['cgroup']).exists():raise
            try:listing=mps('server','list','--format=csv,noheader')
            except subprocess.CalledProcessError:pass
            else:
                if any(row and row[0].strip()==SERVER for row in csv.reader(listing.splitlines())):raise
        (RECORDS/(record['task']+'.json')).unlink()
    def prepare(self,pod,devices):
        metadata=pod['metadata'];task=str(uuid.UUID(metadata['labels']['platform.task']))
        record_file=RECORDS/(task+'.json')
        if record_file.exists():
            record=json.loads(record_file.read_text())
            if record['pod_uid']!=metadata['uid']:raise RuntimeError('GPU lease belongs to another Pod')
        else:
            request=json.loads(metadata['annotations']['gpu.platform/request'])
            validate_gpu_requests(request,len(request))
            allocations=choose_devices(devices,[r['allocations'] for r in self.records()],request)
            if allocations is None:
                self.annotate(pod,'gpu.platform/waiting','等待同卡算力与显存');return
            for a in allocations:a['partition']='p_'+uuid.UUID(task).hex+'_'+str(a['index'])
            record={'task':task,'pod_uid':metadata['uid'],'pod_name':metadata['name'],
                'token':secrets.token_urlsafe(32),'allocations':allocations,'prepared':False}
            save(record)
        name='t_'+uuid.UUID(task).hex
        if not record['prepared']:
            try:
                for a in record['allocations']:
                    output=mps('sm-partition','create',a['partition'],'--server='+SERVER,'--device='+a['uuid'],'--chunks='+str(a['sm_count']//2))
                    if 'created on device' not in output:raise RuntimeError('GPU 分区创建未完成')
                mps('namespace','create',name,'--server='+SERVER)
                for a in record['allocations']:
                    mps('namespace','set',name,'--server='+SERVER,'--device='+a['uuid'],'--sm-partition='+a['partition'])
                record['prepared']=True;save(record)
            except Exception:
                self.release(record);raise
        secret=metadata['annotations']['gpu.platform/config-secret']
        values={'NVIDIA_VISIBLE_DEVICES':','.join(a['uuid'] for a in record['allocations']),
                'PLATFORM_GPU_LEASE':record['token']}
        patch={'stringData':values}
        self.kube('-n',self.ns,'patch','secret',secret,'--type=merge','-p',json.dumps(patch))
        self.annotate(pod,'platform.gpu-devices',json.dumps([a['uuid'] for a in record['allocations']]))
        self.annotate(pod,'gpu.platform/allocations',json.dumps(record['allocations']))
        gates=pod.get('spec',{}).get('schedulingGates',[])
        kept=[g for g in gates if g['name']!=GATE]
        self.kube('-n',self.ns,'patch','pod',metadata['name'],'--type=merge','-p',json.dumps({'spec':{'schedulingGates':kept}}))
    def tick(self):
        with locked():
            devices=cuda_inventory()
            pods=json.loads(self.kube('-n',self.ns,'get','pods','-l','app.kubernetes.io/managed-by=gpu-platform','-o','json'))['items']
            live={p['metadata']['uid']:p for p in pods}
            for record in self.records():
                pod=live.get(record['pod_uid'])
                if pod is None or pod.get('status',{}).get('phase') in {'Succeeded','Failed'}:
                    try:self.release(record)
                    except (OSError,subprocess.SubprocessError,RuntimeError) as exc:
                        log.warning('GPU 回收等待 %s: %s',record['task'],exc)
            try:self.ready(devices)
            except Exception as exc:
                for d in devices:d['ready']=False
                for record in self.records():
                    if record['pod_uid'] in live:self.annotate(live[record['pod_uid']],'gpu.platform/error',str(exc))
            if all(d['ready'] for d in devices):
                for pod in sorted(pods,key=lambda p:p['metadata'].get('creationTimestamp','')):
                    gates=[g['name'] for g in pod.get('spec',{}).get('schedulingGates',[])]
                    if GATE not in gates or 'kueue.x-k8s.io/admission' in gates or pod['metadata'].get('deletionTimestamp'):continue
                    try:self.prepare(pod,devices)
                    except Exception as exc:self.annotate(pod,'gpu.platform/error',(getattr(exc,'output',None) or str(exc))[-1000:])
            leases=self.records()
            for d in devices:
                allocated=[a for r in leases for a in r['allocations'] if a['uuid']==d['uuid']]
                d['used_compute_percent']=sum(a['compute_percent'] for a in allocated)
                d['used_sm_count']=sum(a['sm_count'] for a in allocated)
                d['used_memory_mib']=sum(a['memory_mib'] for a in allocated)
            nodes=json.loads(self.kube('get','nodes','-o','json'))['items'];assert len(nodes)==1
            node=nodes[0]
            capacity={COMPUTE:str(sum(100 for d in devices if d['ready'])),MEMORY:str(sum(d['usable_memory_mib'] for d in devices if d['ready']))}
            if any(node['status'].get('capacity',{}).get(k)!=v for k,v in capacity.items()):
                self.kube('patch','node',node['metadata']['name'],'--subresource=status','--type=merge','-p',json.dumps({'status':{'capacity':capacity}}))
            encoded=json.dumps(devices,separators=(',',':'))
            if node['metadata'].get('annotations',{}).get('platform.gpu-inventory')!=encoded:
                self.kube('annotate','node',node['metadata']['name'],'platform.gpu-inventory='+encoded,'--overwrite')

if __name__=='__main__':
    if os.geteuid()!=0:raise SystemExit('GPU host worker requires root')
    if sys.argv[1:] == ['hook']:quota_hook()
    else:raise SystemExit('Expected hook')
