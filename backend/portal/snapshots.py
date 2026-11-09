import json
import base64
import uuid
from django.db import transaction
from django.db.models import Sum
from .configuration import config
from .models import EnvironmentSnapshot, ImageArtifact, ResourceRequest, Workspace
from .services import audit, request_stop, policy_for

def delete_operation(kube, ns, obj):
    metadata = obj['metadata']
    preconditions = {key: metadata[key] for key in ('uid', 'resourceVersion') if key in metadata}
    kube.call('DELETE', kube.path('configmaps', ns, metadata['name']),
              {'preconditions': preconditions})

def clean_finished_operations(kube, ns):
    operations = {}
    for obj in kube.list('configmaps', ns, 'platform.operation=snapshot'):
        name = obj['metadata']['name']
        if not name.startswith('snapshot-'):
            continue
        try:
            operation_id = uuid.UUID(name.removeprefix('snapshot-'))
        except ValueError:
            continue
        operations[operation_id] = obj
    for pk in EnvironmentSnapshot.objects.filter(pk__in=operations, status__in=['succeeded', 'failed']).values_list('pk', flat=True):
        delete_operation(kube, ns, operations[pk])

def sync_snapshots(kube):
    ns = config()['platform']['namespace']
    clean_finished_operations(kube, ns)
    for snapshot in EnvironmentSnapshot.objects.filter(status__in=['pending', 'working']).select_related('source__user', 'target'):
        name = 'snapshot-' + snapshot.pk.hex
        desired = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'labels': {'platform.operation': 'snapshot'}},
                   'data': {'request': json.dumps({'id': str(snapshot.pk), 'source': str(snapshot.source_id), 'owner': snapshot.source.user_id}), 'status': 'pending'}}
        stopped = snapshot.target_id and Workspace.objects.filter(request_id=snapshot.target_id, stop_requested=True).exists()
        if stopped:
            from .reconcile import mark_finished
            mark_finished(snapshot.target, 'canceled')
            snapshot.target.refresh_from_db(fields=['status'])
        canceled = snapshot.target_id and snapshot.target.status != 'preparing'
        obj = kube.get('configmaps', ns, name) if canceled else kube.ensure(desired, 'configmaps', ns)
        if canceled and (obj is None or obj['data'].get('status') == 'pending'):
            # A worker may claim this operation between GET and DELETE. Preserve
            # its result when resourceVersion changes, then collect the image.
            if obj is not None:
                delete_operation(kube, ns, obj)
            snapshot.status, snapshot.error = 'failed', '运行申请已取消。'
            snapshot.save(update_fields=['status', 'error'])
            continue
        state = obj['data'].get('status')
        if state == 'failed':
            snapshot.status, snapshot.error = 'failed', obj['data'].get('error', '保存失败')
            snapshot.save(update_fields=['status', 'error'])
            if snapshot.target_id:
                from .reconcile import mark_finished
                mark_finished(snapshot.target, 'failed', error=snapshot.error)
            delete_operation(kube, ns, obj)
        elif state == 'succeeded':
            result = json.loads(obj['data']['result'])
            used = ImageArtifact.objects.filter(owner=snapshot.source.user).exclude(state='deleted').aggregate(n=Sum('size_bytes'))['n'] or 0
            policy = policy_for(snapshot.source.user)
            if not policy.unlimited and used + result['size_bytes'] > policy.image_gib * 2**30:
                from .registry import RegistryClient
                cfg = config()
                secret = kube.get('secrets', ns, 'registry-state')
                identity = json.loads(base64.b64decode(secret['data']['users.json']))[str(snapshot.source.user_id)]
                repository, tag = result['reference'].removeprefix(cfg['registry']['host']+'/').rsplit(':', 1)
                RegistryClient(identity).delete_tag(repository, tag)
                snapshot.status, snapshot.error = 'failed', '保存结果超出个人镜像额度。'
                snapshot.save(update_fields=['status', 'error'])
                if snapshot.target_id:
                    from .reconcile import mark_finished
                    mark_finished(snapshot.target, 'failed', error=snapshot.error)
                delete_operation(kube, ns, obj)
                continue
            with transaction.atomic():
                artifact, _ = ImageArtifact.objects.get_or_create(reference=result['reference'], defaults={
                    'owner': snapshot.source.user, 'name': snapshot.source.purpose[:90]+' · 环境', 'digest': result['digest'],
                    'size_bytes': result['size_bytes'], 'architecture': config()['platform']['architecture'], 'verified': True,
                    'temporary': bool(snapshot.target_id)})
                snapshot.status, snapshot.artifact = 'succeeded', artifact
                snapshot.save(update_fields=['status', 'artifact'])
                if snapshot.target_id:
                    changed = ResourceRequest.objects.filter(pk=snapshot.target_id, status='preparing', workspace__stop_requested=False).update(image=artifact,
                        image_reference=result['reference'].split('@')[0]+'@'+result['digest'], status='approved')
                    if changed:
                        request_stop(snapshot.source_id, snapshot.source.user, '环境已保存，转入程序运行')
                audit(snapshot.source.user, 'environment_saved', snapshot.source_id, image=str(artifact.pk))
            delete_operation(kube, ns, obj)
        elif state == 'working':
            EnvironmentSnapshot.objects.filter(pk=snapshot.pk).update(status='working')
