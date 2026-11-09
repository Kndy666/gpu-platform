"""Object visibility shared by the browser portal and Bearer API."""
from .models import ImageArtifact, ResourceRequest, VolumeAllocation


def tasks_for(user, *, own=False):
    query = ResourceRequest.objects.all()
    return query if user.is_staff and not own else query.filter(user=user)


def images_for(user, *, management=False):
    from django.db.models import Q
    query = ImageArtifact.objects.all()
    if management and user.is_staff:
        return query
    return query.filter(Q(owner=user) | Q(shared=True))


def image_actions_for(user):
    query = ImageArtifact.objects.all()
    return query if user.is_staff else query.filter(owner=user)


def volumes_for(user, *, own=False):
    query = VolumeAllocation.objects.select_related('user', 'pool', 'request')
    return query if user.is_staff and not own else query.filter(user=user)


def capabilities(user):
    staff = bool(user.is_active and user.is_staff)
    return {'manage_tasks': staff, 'connect_containers': staff, 'manage_members': staff, 'manage_groups': staff,
            'manage_storage': staff, 'manage_images': staff, 'read_audits': staff,
            'manage_settings': staff, 'grant_admin': bool(staff and user.is_superuser)}
