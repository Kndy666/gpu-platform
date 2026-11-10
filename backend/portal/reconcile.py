import logging
import os
from datetime import timedelta
from pathlib import Path
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .configuration import config
from .kube import KubeClient
from .manifests import pvc, task_objects
from .models import AuditLog, PlatformSettings, ResourceRequest, StoragePool, SystemObservation, VolumeAllocation, Workspace
from .services import ACTIVE, TERMINAL, audit, work_owner, work_volume, volume_in_use
from .resources import quantity

log = logging.getLogger(__name__)


@transaction.atomic
def reserve_dispatch(req, ws):
    from .services import policy_for
    policy = policy_for(req.user)
    ws.refresh_from_db()
    if ws.dispatch_started_at:
        return True
    if not policy.unlimited:
        active = [row.request for row in Workspace.objects.filter(request__user=req.user,
                  cleanup_complete=False, dispatch_started_at__isnull=False).exclude(pk=ws.pk).select_related('request')]
        limits = {'cpu': policy.cpu, 'memory_gib': policy.memory_gib,
                  'gpu_compute_total': policy.gpu_compute_percent, 'gpu_memory_total_gib': policy.gpu_memory_gib}
        if len(active) >= policy.concurrent_tasks or any(sum(getattr(row, key) for row in active) + getattr(req, key) > limit
                                                       for key, limit in limits.items()):
            return False
    ws.dispatch_started_at = timezone.now()
    ws.save(update_fields=['dispatch_started_at'])
    return True

@transaction.atomic
def allocate_port(ws, occupied=()):
    ws.refresh_from_db()
    if ws.ssh_port:
        return
    start, end = config()['platform']['ssh_ports']
    used = set(Workspace.objects.exclude(ssh_port=None).values_list('ssh_port', flat=True)) | set(occupied)
    for port in range(start, end + 1):
        if port not in used:
            ws.ssh_port = port
            ws.save(update_fields=['ssh_port'])
            return
    raise RuntimeError('SSH 端口池耗尽')

@transaction.atomic
def mark_finished(req, status, code=None, error='', ended_at=None):
    ws = Workspace.objects.get(request=req)
    if ws.ended_at:
        return
    now = ended_at or timezone.now()
    ws.ended_at, ws.exit_code, ws.error = now, code, error
    ws.save(update_fields=['ended_at', 'exit_code', 'error'])
    ResourceRequest.objects.filter(pk=req.pk).update(status=status)
    volume = work_volume(req)
    if not volume_in_use(volume, excluding=req.pk):
        VolumeAllocation.objects.filter(pk=volume.pk).update(retain_until=now + timedelta(days=PlatformSettings.load().workspace_retention_days))
    if req.source_environment_id and volume.request_id==req.pk:
        original=work_volume(req.source_environment)
        if original.state not in {'deleting','deleted'} and not volume_in_use(original,excluding=req.pk):
            VolumeAllocation.objects.filter(pk=original.pk).update(retain_until=now+timedelta(days=PlatformSettings.load().workspace_retention_days))
    audit(None, 'finished', req.pk, status=status, exit_code=code, error=error)

class Reconciler:
    def __init__(self, kube=None, node=None):
        from .models import ComputeNode, default_node_id
        self.node = node or ComputeNode.objects.get(pk=default_node_id())
        self.kube = kube or KubeClient(node=self.node)
        self.ns = config()['platform']['workload_namespace']

    def observe(self):
        from .resources import resource_summary
        nodes = self.kube.list('nodes')
        import json
        inventory = []
        for node in nodes:
            inventory.extend(json.loads(node['metadata'].get('annotations', {}).get('platform.gpu-inventory', '[]')))
        SystemObservation.objects.update_or_create(name='gpu_inventory', defaults={'data': {'devices': inventory}})
        pods = self.kube.list('pods')
        from .queues import sync_pool_capacity
        sync_pool_capacity(self.kube, nodes)
        SystemObservation.objects.update_or_create(name='resources', defaults={'data': resource_summary(nodes, pods)})
        remote_pools = getattr(self, 'remote_pools', {})
        for pool in StoragePool.objects.all():
            # The chart mounts only the configured pool paths read-only for statvfs.
            path = Path('/storage-pools') / pool.name
            try:
                if self.node.transport == 'ssh':
                    observed = remote_pools[pool.name]
                    capacity, free = observed['capacity_gib'], observed['free_gib']
                else:
                    stats = os.statvfs(path)
                    capacity, free = stats.f_blocks * stats.f_frsize // 2**30, stats.f_bavail * stats.f_frsize // 2**30
                StoragePool.objects.filter(pk=pool.pk).update(capacity_gib=capacity,
                    free_gib=free, healthy=True, observed_at=timezone.now())
            except (OSError, KeyError):
                StoragePool.objects.filter(pk=pool.pk).update(healthy=False)
        previous = SystemObservation.objects.filter(name='volume_usage').first()
        if not previous or previous.observed_at < timezone.now()-timedelta(seconds=30):
            count = 0
            for node in nodes:
                for pod in self.kube.volume_stats(node).get('pods', []):
                    for entry in pod.get('volume', []):
                        claim = entry.get('pvcRef', {})
                        if claim.get('namespace') == self.ns and entry.get('usedBytes') is not None:
                            count += VolumeAllocation.objects.filter(pvc_name=claim['name']).update(used_bytes=entry['usedBytes'])
            SystemObservation.objects.update_or_create(name='volume_usage', defaults={'data': {'volumes': count}})

    def ensure_volumes(self, req):
        for volume in VolumeAllocation.objects.filter(user=req.user).filter(Q(kind='home') | Q(request=work_owner(req))).exclude(state='deleted').select_related('pool'):
            if volume.state == 'deleting':
                raise RuntimeError('数据卷正在删除，停止派发。')
            if volume.state == 'bound' and self.kube.get('persistentvolumeclaims', self.ns, volume.pvc_name) is None:
                raise RuntimeError(f'已绑定卷 {volume.pvc_name} 缺失，请管理员恢复数据卷。')
            obj = self.kube.ensure(pvc(volume), 'persistentvolumeclaims', self.ns)
            if quantity(obj['spec']['resources']['requests']['storage']) < volume.size_gib * 2**30:
                self.kube.call('PATCH', self.kube.path('persistentvolumeclaims', self.ns, volume.pvc_name),
                               {'spec': {'resources': {'requests': {'storage': f'{volume.size_gib}Gi'}}}})
            if obj.get('status', {}).get('phase') == 'Bound' and volume.state not in {'copy_pending','copying'}:
                VolumeAllocation.objects.filter(pk=volume.pk).update(state='bound')

    def reconcile_task(self, req, ws=None):
        if ws is None:
            ws = Workspace.objects.get(request=req)
        if ws.ended_at:
            self.cleanup(req, ws)
            return
        if ws.stop_requested:
            mark_finished(req, 'canceled')
            self.cleanup(req, ws)
            return
        now = timezone.now()
        if req.mode == 'interactive' and req.ends_at <= now:
            mark_finished(req, 'expired')
            self.cleanup(req, ws)
            return
        if req.status == 'preparing':
            snapshot=getattr(req,'environment_snapshot',None)
            if not snapshot or snapshot.status!='succeeded':return
        if req.source_environment_id and not Workspace.objects.get(request_id=req.source_environment_id).cleanup_complete:
            return
        if req.starts_at and req.starts_at > now:
            return
        if req.mode == 'interactive' and ws.expire_at and ws.expire_at <= now:
            mark_finished(req, 'expired')
            self.cleanup(req, ws)
            return
        if (config()['platform'].get('maintenance') or self.node.maintenance) and not ws.dispatch_started_at:
            return
        self.ensure_volumes(req)
        from .workspace_copy import ensure_workspace_copy
        if not ensure_workspace_copy(self.kube,req):
            req.refresh_from_db(fields=['status'])
            if req.status in TERMINAL:self.cleanup(req,ws)
            else:ResourceRequest.objects.filter(pk=req.pk).update(status='preparing')
            return
        occupied = {p['nodePort'] for s in self.kube.list('services') for p in s.get('spec', {}).get('ports', []) if p.get('nodePort')}
        if self.node.transport == 'ssh':
            from .models import ComputeNode
            local = ComputeNode.objects.get(transport='local')
            occupied |= {p['nodePort'] for s in KubeClient(node=local).list('services') for p in s.get('spec', {}).get('ports', []) if p.get('nodePort')}
        allocate_port(ws, occupied)
        secret, workload, service = task_objects(req, ws)
        current=self.kube.ensure(secret, 'secrets', self.ns)
        from .ssh_access import sync_authorized_keys
        sync_authorized_keys(self.kube,self.ns,req,current,secret['stringData']['authorized_keys'])
        resource, group = ('jobs', 'batch/v1') if req.mode == 'batch' else ('pods', 'v1')
        # After observation of a running Pod, absence must NEVER re-run training.
        obj = self.kube.get(resource, self.ns, ws.object_name, group)
        if obj is None and ws.dispatch_started_at:
            mark_finished(req, 'failed', error='已派发的工作负载缺失，或前次创建结果未知；不会自动重跑，请管理员检查。')
            self.cleanup(req, ws)
            return
        if obj is None:
            if not reserve_dispatch(req, ws):
                ResourceRequest.objects.filter(pk=req.pk).update(status='queued')
                return
            self.kube.ensure(workload, resource, self.ns, group)
        self.kube.ensure(service, 'services', self.ns)
        Workspace.objects.filter(pk=ws.pk).update(error='')
        pods = self.kube.list('pods', self.ns, f'platform.task={req.pk}')
        if req.mode == 'interactive' and not pods:
            pods = [obj] if obj else []
        status = 'queued'
        for pod in pods:
            gpu_error=pod.get('metadata',{}).get('annotations',{}).get('gpu.platform/error')
            if gpu_error:
                mark_finished(req,'failed',error=gpu_error)
                self.cleanup(req,ws)
                return
            phase = pod.get('status', {}).get('phase')
            statuses = pod.get('status', {}).get('containerStatuses', [])
            terminated = next((s.get('state', {}).get('terminated') for s in statuses if s.get('name') == 'workspace'), None)
            if terminated:
                code = terminated['exitCode']
                import json
                if not ws.started_at and terminated.get('startedAt'):
                    ws.started_at = parse_datetime(terminated['startedAt'])
                ws.gpu_devices = json.loads(pod['metadata'].get('annotations', {}).get('platform.gpu-devices', '[]')) or ws.gpu_devices
                ws.gpu_allocations=json.loads(pod['metadata'].get('annotations',{}).get('gpu.platform/allocations','[]')) or ws.gpu_allocations
                ws.save(update_fields=['started_at', 'gpu_devices','gpu_allocations'])
                ended_at = parse_datetime(terminated['finishedAt']) if terminated.get('finishedAt') else None
                mark_finished(req, 'succeeded' if code == 0 else 'failed', code, terminated.get('message', ''), ended_at=ended_at)
                self.cleanup(req, ws)
                return
            if phase in {'Failed', 'Succeeded'}:
                mark_finished(req, 'failed', error=pod.get('status', {}).get('reason', '未能获取退出码'))
                self.cleanup(req, ws)
                return
            if phase == 'Running':
                status = 'running' if any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', [])) else 'starting'
                if not ws.started_at:
                    ws.started_at = now
                    ws.expire_at = req.ends_at if req.mode == 'interactive' else None
                import json
                ws.gpu_devices = json.loads(pod['metadata'].get('annotations', {}).get('platform.gpu-devices', '[]'))
                ws.gpu_allocations=json.loads(pod['metadata'].get('annotations',{}).get('gpu.platform/allocations','[]'))
                ws.error = ''
                ws.save(update_fields=['started_at', 'expire_at', 'gpu_devices','gpu_allocations', 'error'])
            elif not pod.get('spec', {}).get('schedulingGates'):
                status = 'starting'
            waiting = [s.get('state', {}).get('waiting', {}) for s in statuses]
            errors = [f"{v.get('reason', '')}: {v.get('message', '')}" for v in waiting if v.get('reason') not in {None, 'ContainerCreating', 'PodInitializing'}]
            if errors:
                Workspace.objects.filter(pk=ws.pk).update(error='\n'.join(errors)[:4000])
        ResourceRequest.objects.filter(pk=req.pk, status__in=ACTIVE).update(status=status)
        if req.mode == 'batch' and ws.started_at and now - ws.started_at > timedelta(hours=config()['platform']['batch_reminder_hours']):
            if not AuditLog.objects.filter(action='long_batch', target=str(req.pk)).exists():
                audit(None, 'long_batch', req.pk, message='命令任务运行较久，仅提醒，不终止')

    def cleanup(self, req, ws):
        if ws.cleanup_complete:
            return
        services_gone = self.kube.delete_owned('services', self.ns, ws.object_name, req.pk)
        resource, group = ('jobs', 'batch/v1') if req.mode == 'batch' else ('pods', 'v1')
        workload_gone = self.kube.delete_owned(resource, self.ns, ws.object_name, req.pk, group)
        copy_gone=True
        if req.source_environment_id and getattr(req,'volumeallocation',None):
            from .workspace_copy import copy_name
            copy_gone=self.kube.delete_owned('jobs',self.ns,copy_name(req),req.pk,'batch/v1')
        pods = self.kube.list('pods', self.ns, f'platform.task={req.pk}')
        for pod in pods:
            self.kube.delete_owned('pods', self.ns, pod['metadata']['name'], req.pk)
        secret_gone = self.kube.delete_owned('secrets', self.ns, ws.object_name, req.pk)
        if services_gone and workload_gone and copy_gone and not pods and secret_gone:
            Workspace.objects.filter(pk=ws.pk).update(cleanup_complete=True, ssh_port=None)
            audit(None, 'resources_released', req.pk)

    def collect(self):
        from .volumes import volume_busy, request_volume_deletion, collect_volume_deletions
        now = timezone.now()
        for volume in VolumeAllocation.objects.filter(kind='work', retain_until__lte=now + timedelta(days=1)).exclude(state='deleted'):
            if volume.state == 'deleting' or volume_busy(volume):
                continue
            ws = Workspace.objects.get(request=volume.request)
            if not ws.cleanup_complete:
                continue
            if not volume.notified_at:
                # Notify during the final day of the configured retention window.
                volume.notified_at = timezone.now()
                volume.save(update_fields=['notified_at'])
                audit(None, 'volume_deletion_notice', volume.request_id, pvc=volume.pvc_name)
            if volume.retain_until > now:
                continue
            request_volume_deletion(volume.pk, None)
        collect_volume_deletions(self.kube)

    def tick(self):
        from .node_context import using_node
        from .node_transport import node_call, request_budget
        from .models import ComputeNode
        with using_node(self.node), request_budget(45):
            if self.node.transport == 'ssh':
                try:
                    observation = node_call(self.node, 'observe')
                    if observation.get('protocol') != 1:
                        raise RuntimeError('节点协议版本不一致，请更新节点代码')
                    self.node.runtime_config = observation['config']
                    self.node.architecture = observation['config']['platform']['architecture']
                    self.node.last_seen, self.node.state, self.node.error = timezone.now(), 'online', ''
                    self.node.save(update_fields=['runtime_config', 'architecture', 'last_seen', 'state', 'error'])
                    self.remote_pools = {pool['name']: pool for pool in observation['pools']}
                    for pool in observation['pools']:
                        StoragePool.objects.update_or_create(name=pool['name'], defaults={
                            key: pool[key] for key in ('path', 'storage_class', 'buffer_percent', 'capacity_gib', 'free_gib')})
                    self.ns = config()['platform']['workload_namespace']
                    from .builds import ensure_base_build
                    ensure_base_build()
                except Exception as exc:
                    ComputeNode.objects.filter(pk=self.node.pk).update(state='offline', error=str(exc)[:1000])
                    StoragePool.objects.update(healthy=False)
                    # An unreachable node must never be mistaken for missing work.
                    return
            self._tick()
            if self.node.transport == 'local':
                ComputeNode.objects.filter(pk=self.node.pk).update(state='online', last_seen=timezone.now())
            if self.node.transport == 'ssh':
                ports = list(Workspace.objects.filter(request__node=self.node, cleanup_complete=False,
                    request__status__in=ACTIVE).exclude(ssh_port=None).values_list('ssh_port', flat=True))
                try:
                    node_call(self.node, 'forwards', ports=ports)
                except Exception as exc:
                    ComputeNode.objects.filter(pk=self.node.pk).update(error='SSH 转发待恢复：' + str(exc)[:800])
                    Workspace.objects.filter(request__node=self.node, request__status='running').update(error='SSH 转发待恢复，请稍后重连。')

    def _tick(self):
        errors = []
        try:
            from .builds import BuildReconciler
            BuildReconciler(self.kube).tick()
        except Exception as e:
            log.exception('镜像仓库和构建同步失败')
            errors.append(str(e))
        try:
            from .snapshots import sync_snapshots
            sync_snapshots(self.kube)
        except Exception as e:
            log.exception('环境保存失败')
            errors.append(str(e))
        try:
            from .queues import sync_user_queues
            sync_user_queues(self.kube)
        except Exception as e:
            log.exception('用户队列配额同步失败')
            errors.append(str(e))
        try:
            self.observe()
        except Exception as e:
            log.exception('资源观测失败')
            errors.append(str(e))
        for req in ResourceRequest.objects.filter(status__in=ACTIVE | TERMINAL, workspace__cleanup_complete=False).select_related('user__profile', 'image', 'workspace'):
            try:
                self.reconcile_task(req, req.workspace)
            except Exception as e:
                log.exception('任务同步失败 %s', req.pk)
                Workspace.objects.filter(request=req).update(error=str(e)[:4000])
                errors.append(str(e))
        try:
            self.collect()
        except Exception as e:
            log.exception('工作卷回收失败')
            errors.append(str(e))
        try:
            from .image_lifecycle import collect_images
            collect_images(self.kube)
        except Exception as e:
            log.exception('镜像回收失败')
            errors.append(str(e))
        SystemObservation.objects.update_or_create(name='reconciler', defaults={'data': {'errors': errors[:20]}})
