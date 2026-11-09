import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q

from .configuration import config
from .models import EnvironmentSnapshot, ResourceRequest, StoragePool, VolumeAllocation
from .services import ACTIVE, audit

log = logging.getLogger(__name__)


def related_requests(volume):
    requests = ResourceRequest.objects.filter(user_id=volume.user_id)
    if volume.kind == 'work':
        requests = requests.filter(Q(pk=volume.request_id) | (Q(source_environment_id=volume.request_id) &
            (Q(volumeallocation__isnull=True)|Q(volumeallocation__state__in=['copy_pending','copying']))))
    return requests


def volume_busy(volume):
    requests = related_requests(volume)
    return (requests.filter(Q(status__in=ACTIVE | {'submitted'}) | Q(workspace__cleanup_complete=False)).exists()
            or EnvironmentSnapshot.objects.filter(source__in=requests, status__in=['pending', 'working']).exists())


@transaction.atomic
def request_volume_deletion(pk, actor):
    if actor is not None and not actor.is_staff:
        raise ValidationError('需要管理员权限。')
    volume = VolumeAllocation.objects.get(pk=pk)
    if volume.state in {'deleted', 'deleting'}:
        return
    if actor is None and volume.kind != 'work':
        raise ValidationError('个人卷需要管理员操作。')
    if volume_busy(volume):
        raise ValidationError('数据卷仍被任务或环境保存操作使用，请先停止任务并等待资源释放。')
    volume.state, volume.deletion_error = 'deleting', ''
    volume.save(update_fields=['state', 'deletion_error'])
    audit(actor, 'volume_delete_requested', volume.pk, pvc=volume.pvc_name, kind=volume.kind,
          username=volume.user.username, size_gib=volume.size_gib)


def collect_volume_deletions(kube):
    namespace = config()['platform']['workload_namespace']
    candidates = list(VolumeAllocation.objects.filter(state='deleting'))
    if not candidates:
        return
    mounted = {entry['persistentVolumeClaim']['claimName'] for pod in kube.list('pods', namespace)
               for entry in pod.get('spec', {}).get('volumes', []) if 'persistentVolumeClaim' in entry}
    for volume in candidates:
        try:
            if volume_busy(volume) or volume.pvc_name in mounted:
                raise ValidationError('等待使用此卷的容器和环境保存操作释放。')
            claim = kube.get('persistentvolumeclaims', namespace, volume.pvc_name)
            if claim:
                labels = claim['metadata'].get('labels', {})
                if labels.get('platform.volume') != str(volume.pk) or labels.get('platform.user') != str(volume.user_id):
                    raise RuntimeError('PVC 所有权不符，停止删除。')
                pv_name = claim.get('spec', {}).get('volumeName', '')
                if pv_name and pv_name != volume.deletion_pv_name:
                    volume.deletion_pv_name = pv_name
                    volume.save(update_fields=['deletion_pv_name'])
                if not claim['metadata'].get('deletionTimestamp'):
                    kube.call('DELETE', kube.path('persistentvolumeclaims', namespace, volume.pvc_name),
                              {'preconditions': {'uid': claim['metadata']['uid']}})
                continue
            if volume.deletion_pv_name and kube.get('persistentvolumes', None, volume.deletion_pv_name):
                continue
            with transaction.atomic():
                volume.refresh_from_db()
                if volume.state != 'deleting' or volume_busy(volume):
                    continue
                volume.state, volume.deletion_error = 'deleted', ''
                volume.save(update_fields=['state', 'deletion_error'])
                StoragePool.objects.filter(pk=volume.pool_id).update(reserved_gib=F('reserved_gib') - volume.size_gib)
                if volume.kind == 'work':
                    ResourceRequest.objects.filter(pk=volume.request_id).update(status='cleaned')
                audit(None, 'volume_deleted', volume.pk, pvc=volume.pvc_name, kind=volume.kind,
                      size_gib=volume.size_gib)
        except Exception as exc:
            log.warning('数据卷删除等待或失败 %s: %s', volume.pk, exc)
            VolumeAllocation.objects.filter(pk=volume.pk, state='deleting').update(deletion_error=str(exc)[:2000])


def annotate_volume_usage(volumes):
    owners = {volume.user_id for volume in volumes}
    requests = ResourceRequest.objects.filter(user_id__in=owners).filter(
        Q(status__in=ACTIVE | {'submitted'}) | Q(workspace__cleanup_complete=False))
    busy_users, busy_requests = set(), set()
    for user_id, pk, source in requests.values_list('user_id', 'pk', 'source_environment_id'):
        busy_users.add(user_id)
        busy_requests.update({pk, source})
    for user_id, source in EnvironmentSnapshot.objects.filter(source__user_id__in=owners, status__in=['pending', 'working']).values_list('source__user_id', 'source_id'):
        busy_users.add(user_id)
        busy_requests.add(source)
    for volume in volumes:
        volume.busy = volume.user_id in busy_users if volume.kind == 'home' else volume.request_id in busy_requests
