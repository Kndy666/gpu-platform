"""Account-scoped teardown for live workspace checks."""
import json
import subprocess

ACCOUNT_CLEANUP = '''import json,sys,time
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone
from portal.models import ResourceRequest,Workspace,EnvironmentSnapshot,ImageArtifact,ImageBuild,VolumeAllocation,AuditLog
from portal.services import request_stop,ACTIVE
from portal.volumes import request_volume_deletion
from portal.registry import RegistryClient,decode
from portal.configuration import config
from portal.kube import KubeClient
data=json.load(sys.stdin);name=data['username'];assert name.startswith(data['prefix'])
user=get_user_model().objects.filter(username=name).first();kube=KubeClient()
if user:
 uid=user.pk;requests=ResourceRequest.objects.filter(user=user);ids={str(v) for v in requests.values_list('pk',flat=True)}
 for request in requests.filter(status__in=ACTIVE):request_stop(request.pk,user)
 deadline=time.monotonic()+180
 while Workspace.objects.filter(request__user=user,cleanup_complete=False).exists():
  if time.monotonic()>deadline:raise RuntimeError('Test compute resources still active')
  time.sleep(2)
 for volume in VolumeAllocation.objects.filter(user=user).exclude(state='deleted'):
  request_volume_deletion(volume.pk,get_user_model().objects.get(username='admin'))
 ImageArtifact.objects.filter(owner=user).exclude(state='deleted').update(recycle_after=timezone.now(),cleanup_retry_at=None)
 deadline=time.monotonic()+180
 while VolumeAllocation.objects.filter(user=user).exclude(state='deleted').exists() or ImageArtifact.objects.filter(owner=user).exclude(state='deleted').exists():
  if time.monotonic()>deadline:raise RuntimeError('Test storage or images still pending cleanup')
  time.sleep(2)
 if data.get('docker_probe'):
  state=kube.get('secrets',config()['platform']['namespace'],'registry-state')
  identity=json.loads(decode(state['data']['users.json'])).get(str(uid))
  if identity:RegistryClient(identity).delete_tag('user-'+str(uid)+'/docker-e2e','denied')
 for model,query in [(EnvironmentSnapshot,{'source__user':user}),(Workspace,{'request__user':user}),
                     (VolumeAllocation,{'user':user}),(ImageArtifact,{'owner':user})]:
  ids|={str(v) for v in model.objects.filter(**query).values_list('pk',flat=True)}
 EnvironmentSnapshot.objects.filter(source__user=user).delete()
 Workspace.objects.filter(request__user=user).delete()
 VolumeAllocation.objects.filter(user=user).delete()
 for request in requests.order_by('-created_at'):request.delete()
 ImageArtifact.objects.filter(owner=user).delete();ImageBuild.objects.filter(user=user).delete()
 AuditLog.objects.filter(actor=user).delete();AuditLog.objects.filter(target__in=ids).delete()
 for session in Session.objects.all():
  if session.get_decoded().get('api_user_id')==uid:session.delete()
 user.delete()
 for resource,namespace,obj in [('secrets',config()['platform']['workload_namespace'],'registry-u'+str(uid)),
    ('secrets',config()['platform']['build_namespace'],'registry-u'+str(uid))]:
  group='v1' if resource=='secrets' else 'kueue.x-k8s.io/v1beta1';existing=kube.get(resource,namespace,obj,group)
  if existing:
   assert existing['metadata']['labels'].get('platform.user')==str(uid)
   kube.call('DELETE',kube.path(resource,namespace,obj,group),{'preconditions':{'uid':existing['metadata']['uid']}})
for token in data.get('tokens',[]):Session.objects.filter(session_key=token.removeprefix('gpa_')).delete()
print(json.dumps({'test_account_removed':not get_user_model().objects.filter(username=name).exists()}))'''

def cleanup_account(app,cfg,username,credentials,prefix,*,docker_probe=False):
    if not credentials:return
    result=app(ACCOUNT_CLEANUP,{'username':username,'prefix':prefix,'docker_probe':docker_probe,'tokens':[credentials[k] for k in ['student','admin'] if k in credentials]})
    print('CLEANUP',result,flush=True)
    if credentials.get('group'):
        app('import json,sys;from portal.models import UserGroup;d=json.load(sys.stdin);UserGroup.objects.filter(pk=d["id"],name=d["name"]).delete();print(json.dumps(True))',{'id':credentials['group'],'name':username})
    uid=credentials['user_id']
    for kind,name,namespace in [('clusterqueue','platform-u'+str(uid),None),('localqueue','user-'+str(uid),cfg['platform']['workload_namespace'])]:
        command=['k3s','kubectl']+(['-n',namespace] if namespace else [])
        found=subprocess.run([*command,'get',kind,name,'--ignore-not-found','-o','json'],text=True,capture_output=True,check=True)
        if found.stdout.strip():
            obj=json.loads(found.stdout)
            assert obj['metadata']['labels'].get('platform.user')==str(uid)
            subprocess.run([*command,'delete',kind,name,'--wait=true'],check=True)
