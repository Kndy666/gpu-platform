"""Durable, rate-limited PushPlus delivery outside scheduling transactions."""
import base64
from datetime import timedelta
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
import requests

from .configuration import base_config
from .models import Notification, NotificationSettings, ResourceRequest

EVENTS = {
    'submit': '新申请', 'approve': '申请通过', 'reject': '申请拒绝',
    'extension_submitted': '延期申请', 'extension_approved': '延期通过', 'extension_rejected': '延期拒绝',
    'running': '容器启动', 'finished': '任务结束', 'ending_soon': '环境即将到期',
    'volume_deletion_notice': '工作卷即将回收', 'node_offline': '节点离线',
    'image_build_succeeded': '镜像构建完成', 'image_build_failed': '镜像构建失败',
    'environment_saved': '环境保存完成', 'long_batch': '程序长时间运行',
}


def cipher():
    key = hashlib.sha256(('gpu-platform-push-v1:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encode_token(token):
    return cipher().encrypt(token.encode()).decode()


def enqueue_event(row):
    if row.action not in EVENTS:
        return
    config = NotificationSettings.objects.filter(pk=1, enabled=True).first()
    if not config or row.action not in config.events:
        return
    title = EVENTS[row.action]
    lines = []
    try:
        req = ResourceRequest.objects.select_related('user', 'node').filter(pk=row.target).first()
    except (ValueError, ValidationError):
        req = None
    if req:
        title += ' · ' + req.purpose[:50]
        lines.extend([f'任务：{req.purpose}', f'用户：{req.user.username}', f'节点：{req.node.name}'])
        origin = base_config()['platform']['https_origin'].rstrip('/')
        lines.append(f'详情：{origin}/tasks/{req.pk}/')
    for key, label in [('reason', '原因'), ('status', '状态'), ('ends_at', '结束时间'), ('name', '名称')]:
        if row.detail.get(key):
            lines.append(f'{label}：{str(row.detail[key])[:500]}')
    lines.append('时间：' + timezone.localtime(row.created_at).strftime('%Y-%m-%d %H:%M:%S'))
    Notification.objects.get_or_create(event_key='audit:' + str(row.pk), defaults={
        'event': row.action, 'title': title[:100], 'content': '\n\n'.join(lines)})


def collect_expiry_reminders():
    config = NotificationSettings.objects.filter(pk=1, enabled=True).first()
    if not config or 'ending_soon' not in config.events:
        return
    now = timezone.now()
    requests = ResourceRequest.objects.filter(mode='interactive', status='running', ends_at__gt=now,
        ends_at__lte=now + timedelta(minutes=config.reminder_minutes), workspace__stop_requested=False)
    from .models import AuditLog
    from .services import audit
    for req in requests:
        end = req.ends_at.isoformat()
        if not AuditLog.objects.filter(action='ending_soon', target=str(req.pk), detail__ends_at=end).exists():
            audit(None, 'ending_soon', req.pk, ends_at=end)


def deliver_one():
    now = timezone.now()
    with transaction.atomic():
        config = NotificationSettings.objects.select_for_update().filter(pk=1, enabled=True).first()
        if not config or not config.token_ciphertext:
            return False
        date = timezone.localdate()
        if config.quota_date != date:
            config.quota_date, config.quota_used = date, 0
        if config.quota_used >= 200 or (config.last_attempt_at and now - config.last_attempt_at < timedelta(seconds=13)):
            return False
        job = Notification.objects.select_for_update().filter(Q(state='pending') | Q(state='sending', next_attempt_at__lte=now),
            next_attempt_at__lte=now, event__in=config.events).order_by('created_at', 'pk').first()
        if not job:
            return False
        job.state, job.attempts, job.next_attempt_at = 'sending', job.attempts + 1, now + timedelta(minutes=5)
        job.save(update_fields=['state', 'attempts', 'next_attempt_at'])
        config.last_attempt_at, config.quota_used = now, config.quota_used + 1
        config.save(update_fields=['last_attempt_at', 'quota_date', 'quota_used'])
    try:
        token = cipher().decrypt(config.token_ciphertext.encode()).decode()
        response = requests.post('https://www.pushplus.plus/send', json={'token': token, 'title': job.title,
            'content': job.content, 'template': 'txt', 'channel': 'wechat'}, timeout=(3, 8), allow_redirects=False)
        response.raise_for_status()
        result = response.json()
        if result.get('code') != 200:
            raise ValueError('渠道返回码 ' + str(result.get('code')))
    except Exception as exc:
        # Keep tokens and provider response bodies out of errors and logs.
        retry = job.attempts < 3
        Notification.objects.filter(pk=job.pk).update(state='pending' if retry else 'failed',
            error=str(exc) if isinstance(exc, ValueError) and str(exc).startswith('渠道返回码 ') else type(exc).__name__,
            next_attempt_at=now + timedelta(seconds=60 * 2**job.attempts))
    else:
        Notification.objects.filter(pk=job.pk).update(state='sent', sent_at=timezone.now(), error='')
    return True
