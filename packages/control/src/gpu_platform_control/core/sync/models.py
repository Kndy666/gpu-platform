import uuid
from django.utils.translation import gettext_lazy as tr
from django.conf import settings
from django.db import models
from django.db.models import Q


class SyncJob(models.Model):
    policy_key = models.CharField(max_length=64, blank=True, db_index=True)
    publish_shared = models.BooleanField(default=False)
    cleanup_image = models.JSONField(default=dict, blank=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=10, choices=[('image', tr('Image')), ('datasets', tr('Shared datasets'))])
    source = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, related_name='outgoing_syncs')
    target = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, related_name='incoming_syncs')
    image = models.ForeignKey('platform_retention.ImageArtifact', null=True, on_delete=models.PROTECT, related_name='syncs')
    result_image = models.ForeignKey('platform_retention.ImageArtifact', null=True, on_delete=models.PROTECT, related_name='source_syncs')
    paths = models.JSONField(default=list)
    state = models.CharField(max_length=12, default='queued')
    reserved_bytes = models.PositiveBigIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(default=0)
    transferred_bytes = models.PositiveBigIntegerField(default=0)
    bandwidth_bytes_per_second = models.PositiveBigIntegerField(default=0)
    error = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)

    @property
    def phase(self):
        if self.state != 'running':
            return self.state
        if self.total_bytes > 0 and self.transferred_bytes >= self.total_bytes:
            return 'applying'
        if self.transferred_bytes > 0:
            return 'transferring'
        return 'preparing'

    class Meta:
        db_table = 'portal_syncjob'
        constraints = [models.UniqueConstraint(fields=['owner', 'image', 'target'], condition=Q(kind='image', state__in=['queued', 'running', 'cleaning']), name='unique_active_image_sync')]
