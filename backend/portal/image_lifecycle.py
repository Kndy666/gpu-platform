import base64
import json
import logging
import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .configuration import config
from .models import ImageArtifact, PlatformSettings, ResourceRequest, Workspace
from .registry import RegistryClient
from .services import TERMINAL, audit

log = logging.getLogger(__name__)


def in_use(image):
    return (ResourceRequest.objects.filter(image=image).exclude(status__in=TERMINAL).exists()
            or Workspace.objects.filter(request__image=image, cleanup_complete=False).exists()
            or ImageArtifact.objects.filter(reference=image.reference).exclude(pk=image.pk).exclude(state='deleted').exists())


@transaction.atomic
def keep_image(pk, actor):
    image = ImageArtifact.objects.get(pk=pk)
    if image.owner_id != actor.pk and not actor.is_staff:
        raise ValidationError('无权操作此镜像。')
    if image.state != 'ready':
        raise ValidationError('镜像已经开始回收。')
    image.temporary, image.recycle_after = False, None
    image.save(update_fields=['temporary', 'recycle_after'])
    audit(actor, 'image_kept', image.pk, name=image.name)


@transaction.atomic
def recycle_image(pk, actor):
    image = ImageArtifact.objects.get(pk=pk)
    if image.owner_id != actor.pk and not actor.is_staff:
        raise ValidationError('无权操作此镜像。')
    if image.shared or image.owner_id is None:
        raise ValidationError('共享基础镜像继续保留。')
    if image.state != 'ready' or in_use(image):
        raise ValidationError('镜像仍在使用，释放任务后可回收。')
    image.recycle_after = timezone.now() + timedelta(hours=24)
    image.save(update_fields=['recycle_after'])
    audit(actor, 'image_recycle_requested', image.pk, name=image.name)


@transaction.atomic
def prepare_collection(pk):
    image = ImageArtifact.objects.get(pk=pk)
    if image.shared or image.owner_id is None or image.state == 'deleted':
        return None
    if in_use(image):
        if image.state == 'ready' and image.recycle_after:
            image.recycle_after = None
            image.save(update_fields=['recycle_after'])
        return None
    if image.state == 'deleting':
        return image
    if not image.recycle_after:
        if not image.temporary:
            return None
        settings = PlatformSettings.load()
        failed = ResourceRequest.objects.filter(image=image, status='failed').exists()
        hours = settings.failed_snapshot_hours if failed else settings.snapshot_hours
        image.recycle_after = timezone.now() + timedelta(hours=hours)
        image.save(update_fields=['recycle_after'])
        return None
    if image.recycle_after > timezone.now():
        return None
    image.state, image.verified = 'deleting', False
    image.save(update_fields=['state', 'verified'])
    return image


def collect_images(kube):
    now = timezone.now()
    candidates = ImageArtifact.objects.filter(shared=False).exclude(state='deleted').filter(
        Q(temporary=True) | Q(recycle_after__isnull=False) | Q(state='deleting')).filter(
        Q(cleanup_retry_at__isnull=True) | Q(cleanup_retry_at__lte=now))
    for pk in candidates.values_list('pk', flat=True):
        image = prepare_collection(pk)
        if image is None:
            continue
        try:
            cfg = config()
            prefix = cfg['registry']['host'] + f'/user-{image.owner_id}/'
            if not image.reference.startswith(prefix):
                raise RuntimeError('镜像仓库归属不符，回收已暂停。')
            path = image.reference.removeprefix(cfg['registry']['host'] + '/')
            repository, sep, tag = path.rpartition(':')
            if not sep or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag):
                raise RuntimeError('镜像缺少可回收的版本标签。')
            secret = kube.get('secrets', cfg['platform']['namespace'], 'registry-state')
            identity = (json.loads(base64.b64decode(secret['data']['collector.json'])) if secret['data'].get('collector.json')
                        else json.loads(base64.b64decode(secret['data']['users.json'])).get(str(image.owner_id)))
            if identity is None:
                raise RuntimeError('账号仓库凭证不可用，请启用账号后重试。')
            RegistryClient(identity).delete_tag(repository, tag)
            with transaction.atomic():
                ImageArtifact.objects.filter(pk=pk, state='deleting').update(
                    state='deleted', deleted_at=timezone.now(), cleanup_error='', cleanup_retry_at=None)
                audit(None, 'image_recycled', pk, name=image.name, reference=image.reference, size_bytes=image.size_bytes)
        except Exception as exc:
            log.exception('镜像回收失败 %s', pk)
            ImageArtifact.objects.filter(pk=pk).update(cleanup_error=str(exc)[:2000], cleanup_retry_at=now + timedelta(minutes=5))
