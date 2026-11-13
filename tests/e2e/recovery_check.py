#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from portal_lifecycle import Acceptance, kube

from environment import require_features, require_commands, namespace

def inventory():
    pods = json.loads(kube('-n',namespace('workload_namespace'),'get','pods','-o','json'))['items']
    volumes = json.loads(kube('-n',namespace('workload_namespace'),'get','pvc','-o','json'))['items']
    return {'pods':{p['metadata']['name']:{'uid':p['metadata']['uid'],'containers':[c.get('containerID') for c in p.get('status',{}).get('containerStatuses',[])]} for p in pods if p['metadata']['name'].startswith('task-') and p.get('status',{}).get('phase')=='Running'},
            'volumes':{p['metadata']['name']:p['metadata']['uid'] for p in volumes}}

def ready(timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            kube('get','--raw','/readyz');return
        except subprocess.CalledProcessError:time.sleep(2)
    raise TimeoutError('K3s API recovery')


def main():
    require_features('cluster', 'recovery')
    require_commands('k3s', 'systemctl', 'sudo')
    if subprocess.run(['systemctl','is-active','--quiet','k3s']).returncode:
        from environment import skip
        skip('Recovery scenarios require a local K3s systemd service')
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True);parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args();test=Acceptance(args.base,args.directory);test.prepare()
    marker='/home/'+test.users[1]['username']+'/recovery-'+hashlib.sha256(str(args.directory.resolve()).encode()).hexdigest()[:10]
    script=f'echo start >> {marker}.starts; while true; do date +%s > {marker}.heartbeat; sleep 1; done'
    batch=test.create(test.users[1],'验收 · 服务恢复中的命令任务',mode='batch',argv=['bash','-lc',script])
    test.wait(batch,{'running'})
    before=inventory();(args.directory/'recovery-before.json').write_text(json.dumps(before,indent=2))
    try:
        fault='import os,signal;from pathlib import Path;found=[int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit() and (p/"cmdline").exists() and b"manage.py"+bytes([0])+b"reconcile" in (p/"cmdline").read_bytes()];assert len(found)==1;os.kill(found[0],signal.SIGKILL);print("Reconciler fault injected")'
        kube('-n',namespace(),'exec','deploy/platform','--','python','-c',fault)
        time.sleep(8)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                if test.status(batch)['status']=='running':break
            except Exception:pass
            time.sleep(2)
        else:raise TimeoutError('Application supervisor recovery')
        print('Application supervisor recovered',flush=True)
        subprocess.run(['sudo','systemctl','restart','k3s'],check=True);ready()
        print('K3s API recovered',flush=True)
        kube('-n','openebs','rollout','restart','daemonset/rawfile-rawfile-localpv-node')
        kube('-n','openebs','rollout','status','daemonset/rawfile-rawfile-localpv-node','--timeout=120s')
        time.sleep(5)
        after=inventory();assert after==before,(before,after)
        state=test.wait(batch,{'running'})
        result=test.ssh(batch,f'test "$(wc -l < {marker}.starts)" = 1 && test "$(( $(date +%s) - $(cat {marker}.heartbeat) ))" -lt 15 && echo "continuous command execution PASS"')
        assert 'PASS' in result.stdout
        test.passed('application_k3s_csi_restart_preserves_tasks_and_volumes',tasks=len(after['pods']),volumes=len(after['volumes']))
        (args.directory/'recovery-after.json').write_text(json.dumps(after,indent=2))
    finally:
        test.stop(batch)

if __name__=='__main__':main()
