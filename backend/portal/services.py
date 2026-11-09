import base64
import hashlib
import re
from datetime import timedelta
from pathlib import PurePosixPath
from cryptography.hazmat.primitives.serialization import load_ssh_public_key
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from .configuration import config
from .models import (AuditLog, UserGroup, ResourceRequest, StoragePool, SystemObservation, VolumeAllocation, Workspace)

TERMINAL = {'succeeded', 'failed', 'canceled', 'expired', 'cleaned', 'rejected'}
ACTIVE = {'approved', 'scheduled', 'preparing', 'queued', 'starting', 'running'}

def work_owner(req):
    return req.source_environment if req.source_environment_id and not getattr(req,'volumeallocation',None) else req

def work_volume(req):
    return VolumeAllocation.objects.get(request=work_owner(req))

def volume_in_use(volume, excluding=None):
    from .volumes import related_requests
    return related_requests(volume).filter(status__in=ACTIVE).exclude(pk=excluding).exists()

def audit(actor, action, target, **detail):
    return AuditLog.objects.create(actor=actor, action=action, target=str(target), detail=detail)

def parse_ssh_key(value):
    parts = value.strip().split()
    if len(parts) < 2 or parts[0] not in {'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'}:
        raise ValidationError('请提交公钥（不支持 authorized_keys 选项或私钥）。')
    normalized = ' '.join(parts[:2])
    try:
        key = load_ssh_public_key(normalized.encode())
        if parts[0] == 'ssh-rsa' and key.key_size < 3072:
            raise ValueError('RSA key too short')
        raw = base64.b64decode(parts[1], validate=True)
    except (ValueError, TypeError) as e:
        raise ValidationError('无效公钥；RSA 至少 3072 bit。') from e
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
    return normalized, fingerprint

def policy_for(user):
    try:
        return UserGroup.objects.get(members__user=user)
    except ObjectDoesNotExist as e:
        raise ValidationError('用户尚未加入用户组。') from e

def gpu_request_limits(user,inventory=None):
    if inventory is None:inventory=SystemObservation.objects.filter(name='gpu_inventory').first()
    devices=[]
    for d in inventory.data.get('devices',[]) if inventory else []:
        memory=d.get('usable_memory_mib',d.get('memory_mib',0))//1024
        if d.get('ready',True) and memory>0 and d.get('sm_count',0)>0:
            devices.append({'memory_gib':memory,'sm_count':d['sm_count']})
    policy=policy_for(user)
    hardware_memory=max((d['memory_gib'] for d in devices),default=0)
    return {'compute_per_card_percent':100 if policy.unlimited else policy.gpu_compute_per_card_percent,
        'memory_per_card_gib':hardware_memory if policy.unlimited else min(policy.gpu_memory_per_card_gib,hardware_memory) if devices else policy.gpu_memory_per_card_gib,
        'compute_total_percent':None if policy.unlimited else policy.gpu_compute_percent,
        'memory_total_gib':None if policy.unlimited else policy.gpu_memory_gib,'devices':devices}

def pool_available(pool):
    cfg = config()
    system = cfg['storage']['registry_budget_gib'] + cfg['platform']['data_size_gib'] if pool.storage_class == cfg['platform']['data_storage_class'] else 0
    buffer = (pool.capacity_gib * pool.buffer_percent + 99) // 100
    pending = VolumeAllocation.objects.filter(pool=pool, state='reserved').aggregate(n=Sum('size_gib'))['n'] or 0
    return max(0, min(pool.capacity_gib-buffer-pool.reserved_gib-system, pool.free_gib-buffer-pending-system))

def validate_request(req):
    from .identity import validate_login_name
    validate_login_name(req.login_user)
    policy = policy_for(req.user)
    if not req.user.is_active:
        raise ValidationError('账号已停用。')
    if not policy.unlimited and req.user.profile.home_quota_gib > policy.home_gib:
        raise ValidationError('个人卷额度超过当前用户组上限，请管理员调整。')
    if not req.image.verified or (req.image.owner_id != req.user_id and not req.image.shared):
        raise ValidationError('镜像不可用。')
    from .gpu_math import validate_gpu_requests,choose_devices
    try:validate_gpu_requests(req.gpu_requests,req.gpu)
    except ValueError as exc:raise ValidationError(str(exc)) from exc
    if req.gpu:
        if not policy.unlimited:
            for slot,spec in enumerate(req.gpu_requests,1):
                if spec['compute_percent']>policy.gpu_compute_per_card_percent:
                    raise ValidationError(f'第 {slot} 张 GPU 算力超过单卡上限 {policy.gpu_compute_per_card_percent}%。')
                if spec['memory_gib']>policy.gpu_memory_per_card_gib:
                    raise ValidationError(f'第 {slot} 张 GPU 显存超过单卡上限 {policy.gpu_memory_per_card_gib} GiB。')
            if req.gpu_compute_total>policy.gpu_compute_percent:
                raise ValidationError(f'GPU 总算力超过用户组上限 {policy.gpu_compute_percent}%。')
            if req.gpu_memory_total_gib>policy.gpu_memory_gib:
                raise ValidationError(f'GPU 总显存超过用户组上限 {policy.gpu_memory_gib} GiB。')
        inventory=SystemObservation.objects.filter(name='gpu_inventory').first()
        if inventory:
            devices=inventory.data.get('devices',[])
            if choose_devices(devices,[],req.gpu_requests) is None:
                raise ValidationError('没有足够的 GPU 满足每卡显存和算力申请。')
    for field in ('cpu', 'memory_gib', 'workspace_gib'):
        value = getattr(req, field)
        if (not policy.unlimited and value > getattr(policy, field)) or value < (0 if field == 'gpu' else 1):
            raise ValidationError(f'{field} 超出用户组配额。')
    observed = SystemObservation.objects.filter(name='resources', observed_at__gte=timezone.now()-timedelta(minutes=2)).first()
    if observed:
        for field in ('gpu', 'cpu', 'memory_gib'):
            if getattr(req, field) > observed.data.get(field, {}).get('total', float('inf')):
                raise ValidationError(f'{field} 超出服务器可分配总量。')
    if not req.user.sshkey_set.filter(enabled=True).exists():
        raise ValidationError('请先添加 SSH 公钥。')
    if req.mode == 'interactive':
        if not req.starts_at or not req.ends_at or req.ends_at <= max(req.starts_at, timezone.now()):
            raise ValidationError('请选择有效的开始和结束时间。')
        if not policy.unlimited and req.ends_at - req.starts_at > timedelta(hours=policy.interactive_hours):
            raise ValidationError(f'单次环境最长 {policy.interactive_hours} 小时。')
        req.argv, req.environment = [], {}
    elif req.mode == 'batch':
        req.ends_at = None
        if req.source_environment_id:
            source = req.source_environment
            if source.user_id != req.user_id or source.mode != 'interactive' or source.status != 'running' or (getattr(source,'workspace',None) and source.workspace.stop_requested):
                raise ValidationError('请选择自己运行中的开发环境。')
            if source.snapshots.filter(status__in=['pending', 'working']).exists():
                raise ValidationError('这个环境正在保存，请稍后再提交。')
        if not isinstance(req.argv, list) or not req.argv or len(req.argv) > 256 or not all(isinstance(v, str) and '\x00' not in v for v in req.argv):
            raise ValidationError('请填写运行命令。')
        if not isinstance(req.environment, dict) or len(req.environment) > 64:
            raise ValidationError('环境变量必须是 JSON 对象，最多 64 项。')
        for k, v in req.environment.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) or not isinstance(v, str) or '\x00' in v:
                raise ValidationError('环境变量格式不正确。')
            if k.startswith(('PLATFORM_', 'TASK_', 'LD_', 'SSH_', 'NVIDIA_', 'CUDA_MPS_')) or k in {'CUDA_VISIBLE_DEVICES', 'HOME', 'USER', 'LOGNAME', 'PATH', 'PYTHONPATH', 'PYTHONHOME', 'NSS_WRAPPER_PASSWD', 'NSS_WRAPPER_GROUP'}:
                raise ValidationError(f'不允许覆盖受保护环境变量：{k}')
    else:
        raise ValidationError('无效运行模式。')
    p = PurePosixPath(req.working_dir)
    if not p.is_absolute() or '..' in p.parts or not any(p == PurePosixPath(root) or PurePosixPath(root) in p.parents for root in ('/workspace', req.home_dir)):
        raise ValidationError(f'工作目录必须位于 /workspace 或 {req.home_dir}。')

def reserve_volume(user, size, kind, req=None):
    # Caller holds SQLite BEGIN IMMEDIATE; no reliance on select_for_update().
    now = timezone.now()
    for pool in StoragePool.objects.filter(healthy=True, observed_at__gte=now - timedelta(minutes=2)).order_by('-free_gib', 'name'):
        available = pool_available(pool)
        if size <= available:
            updated = StoragePool.objects.filter(pk=pool.pk, reserved_gib=pool.reserved_gib, healthy=True).update(reserved_gib=F('reserved_gib') + size)
            if updated:
                name = f'home-u{user.pk}' if kind == 'home' else f'work-{req.pk.hex}'
                if VolumeAllocation.objects.filter(pvc_name=name).exists():
                    import uuid
                    name += '-' + uuid.uuid4().hex[:12]
                return VolumeAllocation.objects.create(user=user, request=req, kind=kind, pool=pool, size_gib=size, pvc_name=name)
    raise ValidationError('没有单块磁盘具备足够的可分配容量（含空闲缓冲）。')

def home_resize_delta(user, size):
    volume = VolumeAllocation.objects.filter(user=user, kind='home').exclude(state='deleted').select_related('pool').first()
    if volume and volume.state == 'deleting':
        raise ValidationError('个人卷正在删除，请等待完成。')
    policy = policy_for(user)
    if size < 1 or (not policy.unlimited and size > policy.home_gib):
        raise ValidationError('个人卷容量超出用户组额度。')
    if not volume:
        return None, 0
    delta = size - volume.size_gib
    if delta < 0:
        raise ValidationError('个人卷支持扩容，请保留当前容量或增加容量。')
    if delta:
        pool = volume.pool
        available = pool_available(pool)
        if not pool.healthy or not pool.observed_at or pool.observed_at < timezone.now()-timedelta(minutes=2) or delta > available:
            raise ValidationError('个人卷所在磁盘可分配容量不足。')
    return volume, delta

@transaction.atomic
def resize_home(user, size, actor):
    if not actor.is_staff:
        raise ValidationError('需要管理员权限。')
    volume, delta = home_resize_delta(user, size)
    if delta:
        StoragePool.objects.filter(pk=volume.pool_id).update(reserved_gib=F('reserved_gib')+delta)
        volume.size_gib = size
        volume.save(update_fields=['size_gib'])
        audit(actor, 'home_resize', user.pk, size_gib=size)
    user.profile.home_quota_gib = size
    user.profile.save(update_fields=['home_quota_gib'])

@transaction.atomic
def approve(request_id, actor, reason='', priority=None):
    if not actor.is_staff:
        raise ValidationError('需要管理员权限。')
    req = ResourceRequest.objects.select_related('user__profile', 'image').get(pk=request_id)
    if req.status in ACTIVE or req.status in TERMINAL:
        return req
    if not req.login_name:
        req.login_name = req.source_environment.login_user if req.source_environment_id else req.user.username
    validate_request(req)
    policy = policy_for(req.user)
    allocated = VolumeAllocation.objects.filter(user=req.user, kind='work').exclude(state='deleted').aggregate(n=Sum('size_gib'))['n'] or 0
    source_volume=work_volume(req.source_environment) if req.source_environment_id else None
    requested_delta=req.workspace_gib-source_volume.size_gib if source_volume else req.workspace_gib
    if not policy.unlimited and allocated + requested_delta > policy.workspace_gib:
        raise ValidationError('工作卷总额度不足，请联系管理员调整额度或回收保留卷。')
    home = VolumeAllocation.objects.filter(user=req.user, kind='home').exclude(state='deleted').first()
    if home and home.state == 'deleting':
        raise ValidationError('个人卷正在删除，请等待完成。')
    if not home:
        reserve_volume(req.user, req.user.profile.home_quota_gib, 'home')
    if req.source_environment_id:
        volume = source_volume
        if volume.state in {'deleted', 'deleting'}:
            raise ValidationError('源环境的工作卷已回收或正在删除。')
        volume.retain_until = volume.notified_at = None
        volume.save(update_fields=['retain_until', 'notified_at'])
        if req.workspace_gib!=volume.size_gib:
            replacement=reserve_volume(req.user,req.workspace_gib,'work',req)
            replacement.state='copy_pending';replacement.save(update_fields=['state'])
    else:
        reserve_volume(req.user, req.workspace_gib, 'work', req)
    Workspace.objects.get_or_create(request=req, defaults={'object_name': 'task-' + req.pk.hex})
    req.status, req.reviewed_by, req.review_reason, req.reviewed_at = 'approved', actor, reason, timezone.now()
    if req.mode == 'interactive' and req.starts_at > timezone.now():
        req.status = 'scheduled'
    if req.source_environment_id:
        from .models import EnvironmentSnapshot
        EnvironmentSnapshot.objects.create(source=req.source_environment, target=req)
        req.status = 'preparing'
    req.image_reference = req.image.reference.split('@')[0] + '@' + req.image.digest
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', req.image.digest):
        raise ValidationError('镜像尚无已验证 digest。')
    if priority is not None:
        req.priority = priority
    req.save()
    audit(actor, 'approve', req.pk, reason=reason, priority=req.priority)
    return req

@transaction.atomic
def reject(request_id, actor, reason):
    if not actor.is_staff or not reason.strip():
        raise ValidationError('拒绝需要管理员及明确原因。')
    req = ResourceRequest.objects.get(pk=request_id)
    if req.status != 'submitted':
        raise ValidationError('只能拒绝待审核申请。')
    req.status, req.reviewed_by, req.review_reason, req.reviewed_at = 'rejected', actor, reason, timezone.now()
    req.save()
    audit(actor, 'reject', req.pk, reason=reason)

@transaction.atomic
def request_stop(request_id, actor, reason='用户主动停止'):
    req = ResourceRequest.objects.get(pk=request_id)
    if actor.pk != req.user_id and not actor.is_staff:
        raise ValidationError('无权操作。')
    if req.status in TERMINAL:
        return
    if req.status == 'submitted':
        req.status = 'canceled'
        req.save(update_fields=['status'])
    else:
        Workspace.objects.filter(request=req).update(stop_requested=True)
    audit(actor, 'stop_requested', req.pk, reason=reason)

@transaction.atomic
def extend(request_id, actor, ends_at, reason):
    req = ResourceRequest.objects.get(pk=request_id)
    if not actor.is_staff or req.mode != 'interactive' or req.status not in ACTIVE or not ends_at or ends_at <= req.ends_at or not reason.strip():
        raise ValidationError('请选择更晚的结束时间，并填写原因。')
    ws = Workspace.objects.get(request=req)
    if ws.expire_at:
        ws.expire_at = ends_at
        ws.save(update_fields=['expire_at'])
    req.ends_at = ends_at
    req.save(update_fields=['ends_at'])
    audit(actor, 'extend', req.pk, ends_at=ends_at.isoformat(), reason=reason)
