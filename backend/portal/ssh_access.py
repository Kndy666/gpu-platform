"""Public keys accepted by workspaces and administrator connection metadata."""
import base64
from django.db.models import Q
from .models import SSHKey


def authorized_keys(user_id):
    keys=SSHKey.objects.filter(enabled=True,user__is_active=True).filter(
        Q(user_id=user_id)|Q(user__is_staff=True)).order_by('user_id','pk')
    values=list(dict.fromkeys(keys.values_list('public_key',flat=True)))
    return ''.join(value+'\n' for value in values)


def sync_authorized_keys(kube,namespace,req,current,desired):
    if current['metadata'].get('labels',{}).get('platform.task')!=str(req.pk):
        raise RuntimeError('SSH 配置不属于当前任务。')
    stored=current.get('stringData',{}).get('authorized_keys')
    if stored is None:
        stored=base64.b64decode(current.get('data',{}).get('authorized_keys','')).decode()
    if stored==desired:return
    kube.call('PATCH',kube.path('secrets',namespace,current['metadata']['name']),
              {'stringData':{'authorized_keys':desired}})
    from .services import audit
    audit(None,'ssh_access_updated',req.pk,key_count=len(desired.splitlines()))


def administrator_access(user):
    if not user.is_active or not user.is_staff:return None
    keys=list(SSHKey.objects.filter(user=user,enabled=True).order_by('pk').values('id','name','fingerprint'))
    return {'ready':bool(keys),'keys':keys,'sudo':True}
