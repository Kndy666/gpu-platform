"""Move an environment's stopped work volume to a differently sized claim."""
from django.utils import timezone
from .configuration import config
from .manifests import metadata
from .models import VolumeAllocation
from .services import audit,work_volume

COPY_SCRIPT='''set -eu
source=${1:-/source}
destination=${2:-/destination}
needed=$(du -sx --block-size=1 "$source" | cut -f1)
available=$(df -B1 --output=avail "$destination" | tail -n 1 | tr -d ' ')
if [ "$needed" -ge "$available" ]; then
    printf '目标工作卷容量不足；原工作卷保留。需要 %s 字节，可用 %s 字节。\\n' "$needed" "$available" >&2
    exit 72
fi
cp -a --sparse=always "$source/." "$destination/"
sync -f "$destination"
printf 'WORKSPACE_COPY_COMPLETE\\n'
'''


def copy_name(req):return 'copy-'+req.pk.hex


def copy_job(req,source,destination):
    return {'apiVersion':'batch/v1','kind':'Job','metadata':metadata(copy_name(req),req),
        'spec':{'backoffLimit':0,'activeDeadlineSeconds':86400,'template':{
            'metadata':{'labels':metadata(copy_name(req),req)['labels']},'spec':{
                'serviceAccountName':'workspace','automountServiceAccountToken':False,'restartPolicy':'Never',
                'securityContext':{'runAsUser':0,'runAsGroup':0,'seccompProfile':{'type':'RuntimeDefault'}},
                'containers':[{'name':'copy','image':config()['platform']['app_image'],'imagePullPolicy':'IfNotPresent',
                    'command':['/bin/sh','-c',COPY_SCRIPT],'terminationMessagePolicy':'FallbackToLogsOnError',
                    'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'512Mi'}},
                    'volumeMounts':[{'name':'source','mountPath':'/source','readOnly':True},
                                   {'name':'destination','mountPath':'/destination'}]}],
                'volumes':[{'name':'source','persistentVolumeClaim':{'claimName':source.pvc_name,'readOnly':True}},
                           {'name':'destination','persistentVolumeClaim':{'claimName':destination.pvc_name}}]}}}}


def ensure_workspace_copy(kube,req):
    destination=getattr(req,'volumeallocation',None)
    if not req.source_environment_id or not destination or destination.state not in {'copy_pending','copying'}:
        return True
    ns=config()['platform']['workload_namespace']
    claim=kube.get('persistentvolumeclaims',ns,destination.pvc_name)
    if not claim:return False
    if claim['metadata'].get('deletionTimestamp'):raise RuntimeError('目标工作卷正在删除。')
    source=work_volume(req.source_environment)
    if not req.source_environment.workspace.cleanup_complete:raise RuntimeError('源容器尚未释放。')
    if source.state in {'deleting','deleted'}:raise RuntimeError('原工作卷已不可用。')
    obj=kube.ensure(copy_job(req,source,destination),'jobs',ns,'batch/v1')
    conditions=obj.get('status',{}).get('conditions',[])
    if obj.get('status',{}).get('succeeded') or any(c['type']=='Complete' and c['status']=='True' for c in conditions):
        destination.state='bound';destination.save(update_fields=['state'])
        kube.delete_owned('jobs',ns,copy_name(req),req.pk,'batch/v1')
        # Retire the old claim only after the complete copy has been synced.
        VolumeAllocation.objects.filter(pk=source.pk).update(retain_until=timezone.now())
        from .volumes import request_volume_deletion,volume_busy
        if not volume_busy(source):request_volume_deletion(source.pk,None)
        audit(req.user,'workspace_copied',req.pk,size_gib=destination.size_gib,pvc=destination.pvc_name)
        return True
    failed=next((c for c in conditions if c['type']=='Failed' and c['status']=='True'),None)
    if failed or obj.get('status',{}).get('failed'):
        pods=kube.list('pods',ns,'job-name='+copy_name(req))
        messages=[state.get('state',{}).get('terminated',{}).get('message','')
            for pod in pods for state in pod.get('status',{}).get('containerStatuses',[])]
        message=next((m.strip() for m in messages if m.strip()),'工作卷复制失败，原工作卷已保留。')
        from .reconcile import mark_finished
        mark_finished(req,'failed',error=message[-2000:])
        return False
    if destination.state!='copying':
        destination.state='copying';destination.save(update_fields=['state'])
    return False
