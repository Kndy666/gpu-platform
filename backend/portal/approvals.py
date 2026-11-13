from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ExtensionRequest, ResourceRequest
from .policies import approval_mode, extension_limit
from .services import ACTIVE, approve, audit, extend, policy_for


def submit_request(req):
    audit(req.user, 'submit', req.pk)
    if approval_mode(req.user) == 'automatic':
        return approve(req.pk, None, '按成员审批配置自动通过')
    return req


def validate_extension(req, ends_at):
    ws = getattr(req, 'workspace', None)
    if (req.mode != 'interactive' or req.status not in ACTIVE or not ws or ws.stop_requested or ws.ended_at
            or not req.ends_at or req.ends_at <= timezone.now()):
        raise ValidationError('仅可为尚未结束的开发环境申请延期。')
    if req.extension_count >= extension_limit(req.user):
        raise ValidationError('这个环境的延期次数已用完。')
    if not ends_at or ends_at <= req.ends_at:
        raise ValidationError('请选择更晚的结束时间。')
    policy = policy_for(req.user)
    if not policy.unlimited and ends_at - req.ends_at > timedelta(hours=policy.interactive_hours):
        raise ValidationError(f'每次最多延长 {policy.interactive_hours} 小时，与新建环境一致。')


@transaction.atomic
def request_extension(request_id, actor, ends_at, reason):
    req = ResourceRequest.objects.select_for_update().select_related('user', 'workspace').get(pk=request_id)
    if req.user_id != actor.pk:
        raise ValidationError('只能为自己的环境申请延期。')
    validate_extension(req, ends_at)
    if not reason.strip():
        raise ValidationError('请填写延期原因。')
    if req.extensions.filter(status='pending').exists():
        raise ValidationError('已有一份延期申请正在审批。')
    entry = ExtensionRequest.objects.create(request=req, previous_ends_at=req.ends_at, ends_at=ends_at, reason=reason.strip())
    audit(actor, 'extension_submitted', req.pk, extension_id=str(entry.pk), ends_at=ends_at.isoformat(), reason=reason)
    if approval_mode(actor) == 'automatic':
        return review_extension(entry.pk, None, 'approve', '按成员审批配置自动通过')
    return entry


@transaction.atomic
def review_extension(identifier, actor, action, reason=''):
    if actor is not None and not actor.is_staff:
        raise ValidationError('需要管理员权限。')
    entry = ExtensionRequest.objects.select_for_update().select_related('request__user').get(pk=identifier)
    if entry.status != 'pending':
        raise ValidationError('这份延期申请已处理。')
    req = ResourceRequest.objects.select_for_update().get(pk=entry.request_id)
    if actor is None and approval_mode(req.user) != 'automatic':
        raise ValidationError('该成员需要人工审批。')
    if action == 'approve':
        validate_extension(req, entry.ends_at)
        if entry.previous_ends_at != req.ends_at:
            raise ValidationError('环境结束时间已更改，请重新申请延期。')
        extend(req.pk, actor, entry.ends_at, entry.reason, automatic=actor is None)
        entry.status = 'approved'
    elif action == 'reject' and actor is not None and reason.strip():
        entry.status = 'rejected'
    else:
        raise ValidationError('请选择通过或拒绝，拒绝时填写原因。')
    entry.reviewed_by, entry.review_reason, entry.reviewed_at = actor, reason, timezone.now()
    entry.save(update_fields=['status', 'reviewed_by', 'review_reason', 'reviewed_at'])
    audit(actor, 'extension_' + entry.status, req.pk, extension_id=str(entry.pk), ends_at=entry.ends_at.isoformat(), reason=reason)
    return entry
