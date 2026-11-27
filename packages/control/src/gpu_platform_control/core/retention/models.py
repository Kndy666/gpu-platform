from django.utils.translation import gettext_lazy as tr
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from gpu_platform_control.core.nodes.models import default_node_id


class ImageArtifact(models.Model):
    node_image_id = models.CharField(max_length=255, blank=True)
    node_image_owner = models.CharField(max_length=255, blank=True)
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, default=default_node_id)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name=tr('Owner'))
    name = models.CharField(max_length=120, verbose_name=tr('Name'))
    reference = models.CharField(max_length=500, verbose_name=tr('Image reference'))
    digest = models.CharField(max_length=100, blank=True, verbose_name=tr("Image version identifier"))
    size_bytes = models.PositiveBigIntegerField(default=0, verbose_name=tr('Size (bytes)'))
    architecture = models.CharField(max_length=20, default='arm64', verbose_name=tr('Architecture'))
    shared = models.BooleanField(default=False, verbose_name=tr('Shared'))
    verified = models.BooleanField(default=False, verbose_name=tr('Verified'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=tr('Created'))
    temporary = models.BooleanField(default=False, verbose_name=tr("Temporary image"))
    state = models.CharField(max_length=16, default='ready', choices=[('ready', tr('Available')), ('deleting', tr('Deleting')), ('deleted', tr('Deleted'))], verbose_name=tr('State'))
    recycle_after = models.DateTimeField(null=True, blank=True, verbose_name=tr('Scheduled deletion'))
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Deleted at'))
    cleanup_error = models.TextField(blank=True, verbose_name=tr('Deletion error'))
    cleanup_retry_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'portal_imageartifact'
        verbose_name = tr('Environment images')
        verbose_name_plural = verbose_name

class VolumeAllocation(models.Model):
    handoff = models.JSONField(default=dict, blank=True)
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, default=default_node_id)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=tr('User'))
    request = models.OneToOneField('platform_requests.ResourceRequest', on_delete=models.PROTECT, null=True, blank=True, verbose_name=tr('Resource request'))
    kind = models.CharField(max_length=10, choices=[('home', tr('Home volume')), ('work', tr('Work volume'))], verbose_name=tr('Type'))
    pool = models.ForeignKey('platform_nodes.StoragePool', on_delete=models.PROTECT, verbose_name=tr('Storage pool'))
    size_gib = models.PositiveIntegerField(verbose_name=tr('Capacity GiB'))
    used_bytes = models.PositiveBigIntegerField(null=True, blank=True, verbose_name=tr('Used bytes'))
    location = models.CharField(max_length=1000, blank=True, verbose_name=tr('Volume name'))
    state = models.CharField(max_length=20, default='reserved')
    retain_until = models.DateTimeField(null=True, blank=True, verbose_name=tr('Retain until'))
    notified_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Notified at'))
    deletion_error = models.TextField(blank=True, verbose_name=tr('Deletion error'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=tr('Created'))

    class Meta:
        db_table = 'portal_volumeallocation'
        verbose_name = tr('Volumes')
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['user', 'node'], condition=Q(kind='home') & ~Q(state='deleted'), name='one_home_per_node')]

class EnvironmentSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey('platform_requests.ResourceRequest', on_delete=models.PROTECT, related_name='snapshots', verbose_name=tr('Source environment'))
    target = models.OneToOneField('platform_requests.ResourceRequest', null=True, blank=True, on_delete=models.PROTECT, related_name='environment_snapshot', verbose_name=tr('Target'))
    status = models.CharField(max_length=20, default='pending', choices=[('pending', tr('Waiting to save')), ('working', tr('Saving')), ('succeeded', tr('Saved')), ('failed', tr('Save failed'))], verbose_name=tr('State'))
    artifact = models.ForeignKey('platform_retention.ImageArtifact', null=True, blank=True, on_delete=models.PROTECT, verbose_name=tr('Saved image'))
    error = models.TextField(blank=True, verbose_name=tr('Error'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=tr('Created'))

    class Meta:
        db_table = 'portal_environmentsnapshot'
        constraints = [models.UniqueConstraint(fields=['source'], condition=Q(status__in=['pending', 'working']), name='one_active_environment_save')]
        verbose_name = tr('Environment save')
        verbose_name_plural = verbose_name

class ImageBuild(models.Model):
    dispatch_payload = models.JSONField(default=dict, blank=True)
    context_ref = models.CharField(max_length=500, blank=True)
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, default=default_node_id, verbose_name=tr("Servers"))
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=tr('User'))
    name = models.SlugField(max_length=80, verbose_name=tr('Name'))
    dockerfile = models.TextField(verbose_name='Dockerfile')
    context_git = models.URLField(blank=True, verbose_name=tr('Git URL'))
    bootstrap = models.BooleanField(default=False, editable=False)
    status = models.CharField(max_length=20, default='submitted', choices=[('submitted', tr('Queued for build')), ('building', tr('Building')), ('succeeded', tr('Completed')), ('failed', tr('Failed'))], verbose_name=tr('State'))
    artifact = models.ForeignKey('platform_retention.ImageArtifact', null=True, blank=True, on_delete=models.PROTECT, verbose_name=tr('Saved image'))
    log = models.TextField(blank=True, verbose_name=tr('Build log'))
    error = models.TextField(blank=True, verbose_name=tr('Error'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=tr('Created'))

    class Meta:
        db_table = 'portal_imagebuild'
        verbose_name = tr('Image build')
        verbose_name_plural = verbose_name

class PlatformSettings(models.Model):
    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    snapshot_hours = models.PositiveIntegerField(default=12, verbose_name=tr('Retention after success or cancellation (hours)'))
    failed_snapshot_hours = models.PositiveIntegerField(default=36, verbose_name=tr('Retention after failure (hours)'))
    workspace_retention_days = models.PositiveIntegerField(default=1, verbose_name=tr('Work volume retention (days)'))

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(singleton=1)[0]

    class Meta:
        db_table = 'portal_platformsettings'
