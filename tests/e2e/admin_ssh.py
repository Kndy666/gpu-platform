#!/usr/bin/env python3
"""Verify administrator SSH on a live workspace and remove all test resources."""
from datetime import datetime,timedelta,timezone
import json,os,subprocess,tempfile,time,uuid
from pathlib import Path
import requests,yaml
from workspace_cleanup import cleanup_account

ROOT=Path(__file__).resolve().parents[2]


def main():
    if os.geteuid()!=0:raise SystemExit('Run with sudo')
    cfg=yaml.safe_load((ROOT/'config.yaml').read_text())
    prefix='ssh-check-'+uuid.uuid4().hex[:8]
    print('TEST_PREFIX',prefix,flush=True)
    accounts={};credentials={};result={}
    session=requests.Session();session.trust_env=False
    url=('https://' if cfg['network']['https'] else 'http://')+cfg['platform']['public_host']

    def app(code,data=None):
        response=subprocess.run(['k3s','kubectl','-n',cfg['platform']['namespace'],'exec','-i','deployment/platform','--',
            'python','manage.py','shell','-c',code],input=json.dumps(data or {}),text=True,capture_output=True)
        if response.returncode:raise RuntimeError(response.stderr[-4000:])
        return json.loads(response.stdout.strip().splitlines()[-1])

    def api(token,method,path,data=None,expected=200):
        response=session.request(method,url+'/api/v1'+path,json=data,headers={'Authorization':'Bearer '+token},timeout=20)
        if response.status_code!=expected:raise RuntimeError(f'{method} {path}: {response.status_code} {response.text[:1000]}')
        return response.json().get('data')

    with tempfile.TemporaryDirectory(prefix='gpu-platform-admin-ssh-') as temporary:
        directory=Path(temporary)
        keys={}
        for role in ['owner','administrator','stranger']:
            key=directory/role
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
            keys[role]=key

        def ssh(role,connection,command='id -un'):
            return subprocess.run(['ssh','-T','-i',str(keys[role]),'-p',str(connection['port']),'-o','BatchMode=yes',
                '-o','IdentitiesOnly=yes','-o','ConnectTimeout=4','-o','ConnectionAttempts=1',
                '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(directory/'known_hosts'),
                '-o','HostKeyAlias='+connection['alias'],connection['user']+'@'+connection['host'],command],
                text=True,capture_output=True,timeout=12)

        def wait_access(role,connection,allowed):
            start=time.monotonic();deadline=start+150
            while time.monotonic()<deadline:
                response=ssh(role,connection)
                if allowed and response.returncode==0 and response.stdout.strip()==connection['user']:return round(time.monotonic()-start,1)
                if not allowed and response.returncode==255 and 'Permission denied' in response.stderr:return round(time.monotonic()-start,1)
                time.sleep(3)
            raise TimeoutError(f'SSH {role} did not become {"allowed" if allowed else "denied"}')

        try:
            credentials=app('''import json,sys
from django.contrib.auth import get_user_model
from portal.api_auth import issue_token
from portal.models import ImageArtifact
data=json.load(sys.stdin)
image=ImageArtifact.objects.filter(shared=True,verified=True,state='ready').latest('created_at')
users={}
for role in ['owner','administrator','stranger']:
 user=get_user_model().objects.create_user(data['prefix']+'-'+role,is_staff=(role=='administrator'))
 user.profile.home_quota_gib=1;user.profile.save(update_fields=['home_quota_gib'])
 users[role]={'username':user.username,'user_id':user.pk,'token':issue_token(user,1800,name=data['prefix'])['token']}
admin=get_user_model().objects.get(username='admin')
print(json.dumps({'accounts':users,'admin':issue_token(admin,1800,name=data['prefix'])['token'],'image':str(image.pk)}))''',{'prefix':prefix})
            accounts=credentials['accounts']
            for role in ['owner','stranger']:
                api(accounts[role]['token'],'POST','/keys',{'name':role,'public_key':keys[role].with_suffix('.pub').read_text()},201)
            now=datetime.now(timezone.utc)
            task=api(accounts['owner']['token'],'POST','/tasks',{'purpose':prefix,'image':credentials['image'],'mode':'interactive',
                'gpu':0,'cpu':1,'memory_gib':1,'workspace_gib':1,'starts_at':(now-timedelta(minutes=1)).isoformat(),
                'ends_at':(now+timedelta(hours=1)).isoformat()},201)
            pk=task['id']
            api(credentials['admin'],'POST','/tasks/'+pk+'/review',{'action':'approve','priority':0})
            deadline=time.monotonic()+180;previous=None
            while time.monotonic()<deadline:
                task=api(accounts['owner']['token'],'GET','/tasks/'+pk)
                if task['status']!=previous:print('TASK',task['status'],flush=True);previous=task['status']
                if task['status']=='running':break
                if task['status'] in {'failed','canceled','expired'}:raise RuntimeError(str(task['error']))
                time.sleep(2)
            else:raise TimeoutError('Test container did not start')
            connection=task['connection']
            owner=ssh('owner',connection,'test "$(sudo -n id -u)" = 0 && printf owner-ok')
            assert owner.returncode==0 and owner.stdout=='owner-ok',owner.stderr
            result['owner_access']=True
            assert wait_access('administrator',connection,False)<10
            api(accounts['administrator']['token'],'POST','/keys',{'name':'Administrator verification',
                'public_key':keys['administrator'].with_suffix('.pub').read_text()},201)
            print('VERIFY administrator key added to running container',flush=True)
            result['key_propagation_seconds']=wait_access('administrator',connection,True)
            admin=ssh('administrator',connection,'test "$(sudo -n id -u)" = 0 && printf administrator-ok')
            assert admin.returncode==0 and admin.stdout=='administrator-ok',admin.stderr
            result['administrator_sudo']=True
            listing=api(accounts['administrator']['token'],'GET','/admin/containers?q='+accounts['owner']['username'])
            assert str(pk) in [row['id'] for row in listing['items']]
            access=api(accounts['administrator']['token'],'GET','/tasks/'+pk+'/connection')
            assert access['administrator_access']['ready'] and access['connection']['user']==accounts['owner']['username']
            api(accounts['stranger']['token'],'GET','/admin/containers',expected=403)
            api(accounts['stranger']['token'],'GET','/tasks/'+pk+'/connection',expected=404)
            wait_access('stranger',connection,False);result['stranger_denied']=True
            print('VERIFY revoke administrator role',flush=True)
            api(credentials['admin'],'PATCH','/admin/members/'+str(accounts['administrator']['user_id']),{'is_staff':False})
            api(accounts['administrator']['token'],'GET','/admin/containers',expected=403)
            result['role_revocation_seconds']=wait_access('administrator',connection,False)
            owner=ssh('owner',connection)
            assert owner.returncode==0 and owner.stdout.strip()==accounts['owner']['username']
            print(json.dumps({'admin_ssh':result}),flush=True)
        finally:
            failures=[]
            for role,data in accounts.items():
                try:cleanup_account(app,cfg,data['username'],{'user_id':data['user_id'],'student':data['token'],
                    **({'admin':credentials['admin']} if role=='owner' else {})},prefix)
                except Exception as exc:failures.append(role+': '+str(exc))
            if failures:raise RuntimeError('Cleanup failed: '+'; '.join(failures))


if __name__=='__main__':main()
