#!/usr/bin/env python3
"""Live CPU environment -> saved files -> GPU command handoff; no framework downloads."""
import json,re,secrets,shlex,subprocess,sys,tempfile,time,uuid
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.e2e.portal_lifecycle import Browser,kube

def shell(code,payload=None):
    raw=kube('-n','gpu-platform','exec','-i','deploy/platform','--','python','manage.py','shell','-c',code,data=json.dumps(payload) if payload else None)
    return raw.strip().splitlines()[-1]

def main():
    user='workflow-check-'+uuid.uuid4().hex[:8];password=secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(prefix='gpu-platform-workflow-') as directory:
        key=Path(directory)/'identity';subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
        code='''import json,sys
from django.contrib.auth import get_user_model
from portal.models import ImageArtifact
v=json.load(sys.stdin);u=get_user_model().objects.create_user(v['user'],password=v['password'],is_staff=True)
u.profile.home_quota_gib=1;u.profile.save()
i=ImageArtifact.objects.get(shared=True,reference__contains='/platform-ubuntu-ssh:')
print(json.dumps({'user_id':u.pk,'image':str(i.pk)}))'''
        info=json.loads(shell(code,{'user':user,'password':password}))
        print('ACCOUNT',json.dumps({'username':user,**info}),flush=True)
        browser=Browser('http://121.48.164.220',user,password)
        browser.post('/keys/',{'name':'workflow-check','public_key':key.with_suffix('.pub').read_text()}).raise_for_status()
        now=datetime.now(ZoneInfo('Asia/Shanghai'))
        response=browser.post('/environments/new/',{'image':info['image'],'purpose':'无 GPU 环境验收','gpu':0,'cpu':1,'memory_gib':1,'workspace_gib':1,'starts_at':(now-timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M'),'ends_at':(now+timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M'),'working_dir':'/workspace'},redirect=False)
        assert response.status_code==302,re.sub('<[^>]+>',' ',response.text)[-1800:]
        source=response.headers['Location'].strip('/').split('/')[-1]
        browser.post(f'/tasks/{source}/review/',{'action':'approve','priority':0}).raise_for_status()
        def status(pk):return browser.get(f'/tasks/{pk}/status/').json()
        def wait(pk,goal,timeout=180):
            last=None;deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                s=status(pk)
                if s!=last:print('STATUS',pk,json.dumps(s,ensure_ascii=False),flush=True);last=s
                if s['status'] in goal:return s
                if s['status'] in ['failed','expired','rejected']:raise RuntimeError(str(s))
                time.sleep(2)
            raise RuntimeError('Timed out: '+str(last))
        s=wait(source,{'running'})
        ssh=['ssh','-i',str(key),'-p',str(s['ssh_port']),'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(Path(directory)/'known_hosts'),'-o','HostKeyAlias=task-'+source,s['login_name']+'@121.48.164.220']
        script='''set -eu
! command -v python3
! command -v pip
[ ! -d /usr/local/cuda ]
printf configured > /tmp/environment-marker
printf personal > $HOME/home-marker
printf workspace > /workspace/work-marker
mkdir -p $HOME/project
cat > $HOME/project/train.sh <<'END'
set -eu
[ "$(cat /tmp/environment-marker)" = configured ]
[ "$(cat $HOME/home-marker)" = personal ]
[ "$(cat /workspace/work-marker)" = workspace ]
echo configured-container-reused
nvidia-smi --query-gpu=uuid --format=csv,noheader
sleep 35
echo training-complete
END
'''
        subprocess.run(ssh+[script],check=True)
        print('CPU_ENVIRONMENT_READY_WITHOUT_PYTHON',flush=True)
        response=browser.post('/training/new/',{'source_kind':'environment','source_environment':source,'purpose':'环境转 GPU 程序运行验收','gpu':1,'cpu':1,'memory_gib':1,'working_dir':'/workspace','command':'bash $HOME/project/train.sh'},redirect=False)
        assert response.status_code==302,re.sub('<[^>]+>',' ',response.text)[-1800:]
        target=response.headers['Location'].strip('/').split('/')[-1]
        browser.post(f'/tasks/{target}/review/',{'action':'approve','priority':0}).raise_for_status()
        wait(target,{'running'},240)
        time.sleep(12)
        data=json.loads(shell('''import json;from portal.models import Workspace,EnvironmentSnapshot;from portal.services import work_volume
s=EnvironmentSnapshot.objects.get(target_id='''+repr(target)+''');w=Workspace.objects.get(request_id='''+repr(target)+''');print(json.dumps({'source_status':s.source.status,'snapshot_status':s.status,'gpu_devices':w.gpu_devices,'source_volume':str(work_volume(s.source).pk),'target_volume':str(work_volume(s.target).pk)}))'''))
        assert data['source_volume']==data['target_volume'],data
        assert data['source_status']=='canceled',data
        assert data['snapshot_status']=='succeeded',data
        assert len(data['gpu_devices'])==1,data
        timeline=browser.get('/timeline/').json()
        assert any(b['id']==target and b['row']==data['gpu_devices'][0] for b in timeline['blocks']),timeline
        final=wait(target,{'succeeded'})
        assert final['exit_code']==0,final
        result={'username':user,'user_id':info['user_id'],'source':source,'target':target,'gpu_devices':data['gpu_devices'],'same_work_volume':True,'source_stopped':True,'exit_code':0,'timeline_actual_gpu':True,'base_without_python_cuda':True}
        Path('/tmp/gpu-platform-workflow-result.json').write_text(json.dumps(result,indent=2));print('PASS',json.dumps(result),flush=True)

if __name__=='__main__':main()
