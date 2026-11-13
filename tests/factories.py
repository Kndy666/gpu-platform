from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from portal.models import UserGroup, StoragePool, ImageArtifact, SSHKey, ResourceRequest

def fixtures():
    UserGroup.objects.create(name='临时用户', is_default=True, gpu_compute_percent=400, cpu=80, memory_gib=192, concurrent_tasks=2)
    U = get_user_model()
    admin = U.objects.create_user('admin', password='test-password-2938', is_staff=True, is_superuser=True)
    a = U.objects.create_user('alice', password='test-password-2938')
    b = U.objects.create_user('bob', password='test-password-2938')
    StoragePool.objects.create(name='test', path='/test', storage_class='rawfile-test', capacity_gib=1000,
                               free_gib=1000, healthy=True, observed_at=timezone.now())
    image = ImageArtifact.objects.create(name='Ubuntu 22.04', reference='test/ssh:1', digest='sha256:'+'a'*64, verified=True, shared=True)
    for user in [a, b]:
        key = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
        SSHKey.objects.create(user=user, name='test', public_key=key, fingerprint='test')
    return admin, a, b, image

def task(user, image, **kwargs):
    data = dict(user=user, image=image, gpu=1, cpu=8, memory_gib=32, workspace_gib=100, purpose='Test experiment', mode='interactive', starts_at=timezone.now()-timedelta(minutes=1), ends_at=timezone.now()+timedelta(hours=1))
    data.update(kwargs)
    data.setdefault('gpu_requests',[{'compute_percent':100,'memory_gib':8} for _ in range(data['gpu'])])
    return ResourceRequest.objects.create(**data)


def set_member_settings(user, **values):
    from portal.models import GROUP_QUOTA_FIELDS
    profile = user.profile
    profile.refresh_from_db()
    limits = dict(profile.resource_limits)
    for name, value in values.items():
        if name in {'extension_limit', 'approval_mode'}:
            setattr(profile, name, value)
        else:
            limits[name] = value
    if limits['unlimited']:
        limits.update({name: None for name in GROUP_QUOTA_FIELDS})
    profile.resource_limits = limits
    profile.save(update_fields=['resource_limits', 'extension_limit', 'approval_mode'])


def member_form_data(user, **values):
    from portal.models import GROUP_QUOTA_FIELDS
    from portal.services import policy_for
    policy = policy_for(user)
    data = {name: getattr(policy, name) for name in GROUP_QUOTA_FIELDS}
    data.update(username=user.username, first_name=user.first_name, email=user.email, is_active=user.is_active,
                is_staff=user.is_staff, group=user.profile.group_id, home_quota_gib=user.profile.home_quota_gib,
                unlimited=policy.unlimited, extension_limit=policy.extension_limit, approval_mode=policy.approval_mode)
    data.update(values)
    return data
