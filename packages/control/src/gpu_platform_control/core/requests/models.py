from django.utils.translation import gettext_lazy as tr
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from gpu_platform_control.core.nodes.models import default_node_id


class ResourceRequest(models.Model):
    resource_allocations = models.JSONField(default=dict, blank=True)
    volume_sizes = models.JSONField(default=dict, blank=True)
    resource_quantities = models.JSONField(default=dict, blank=True)
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, default=default_node_id, verbose_name=tr('Compute nodes'))

    class State(models.TextChoices):
        SUBMITTED = ('submitted', tr('Awaiting review'))
        REJECTED = ('rejected', tr('Rejected'))
        APPROVED = ('approved', tr('Approved'))
        SCHEDULED = ('scheduled', tr('Scheduled'))
        PREPARING = ('preparing', tr('Preparing environment'))
        QUEUED = ('queued', tr('Queued'))
        STARTING = ('starting', tr('Starting'))
        RUNNING = ('running', tr('Running'))
        SUCCEEDED = ('succeeded', tr('Succeeded'))
        FAILED = ('failed', tr('Failed'))
        CANCELED = ('canceled', tr('Canceled'))
        EXPIRED = ('expired', tr('Environment expired'))
        CLEANED = ('cleaned', tr('Work volume recycled'))
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=tr('User'))
    image = models.ForeignKey('platform_retention.ImageArtifact', on_delete=models.PROTECT, verbose_name=tr('Images'))
    login_name = models.CharField(max_length=32, blank=True, editable=False)
    image_reference = models.CharField(max_length=600, blank=True)
    extension_count = models.PositiveIntegerField(default=0, verbose_name=tr('Extensions used'))
    purpose = models.CharField(max_length=500, verbose_name=tr('Name'))
    notes = models.TextField(blank=True, verbose_name=tr('Notes'))
    mode = models.CharField(max_length=16, choices=[('interactive', tr('Development environments')), ('batch', tr('Jobs'))], verbose_name=tr('Execution mode'))
    starts_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Starts at'))
    ends_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Ends at'))
    source_environment = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='training_requests', verbose_name=tr('Source development environment'))
    command = models.TextField(blank=True, verbose_name=tr('Command'))
    argv = models.JSONField(default=list, blank=True)
    working_dir = models.CharField(max_length=500, default='/workspace', verbose_name=tr('Working directory'))
    environment = models.JSONField(default=dict, blank=True, verbose_name=tr('Environment variables'))
    priority = models.IntegerField(default=0, verbose_name=tr('Priority'))
    status = models.CharField(max_length=16, choices=State.choices, default=State.SUBMITTED, verbose_name=tr('State'))
    review_reason = models.TextField(blank=True, verbose_name=tr('Review notes'))
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='reviews', verbose_name=tr('Reviewer'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=tr('Created'))
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Reviewed at'))

    class Meta:
        db_table = 'portal_resourcerequest'
        verbose_name = tr('Resource request')
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=Q(mode='interactive', starts_at__isnull=False, ends_at__isnull=False, ends_at__gt=models.F('starts_at')) | Q(mode='batch', ends_at__isnull=True), name='mode_schedule')]

    def __str__(self):
        return self.purpose

    @property
    def login_user(self):
        return self.login_name or self.user.username

    @property
    def home_dir(self):
        return '/home/' + self.login_user

    @property
    def can_stop(self):
        return self.status in {'submitted', 'approved', 'scheduled', 'preparing', 'queued', 'starting', 'running'}

    @property
    def stop_label(self):
        return tr('Stop task') if self.status in {'starting', 'running'} else tr('Cancel task')

    @property
    def stop_pending_label(self):
        return tr('Stopping') if self.status in {'starting', 'running'} else tr('Canceling')

class ExtensionRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey('platform_requests.ResourceRequest', on_delete=models.PROTECT, related_name='extensions')
    previous_ends_at = models.DateTimeField()
    ends_at = models.DateTimeField(verbose_name=tr('Extend until'))
    reason = models.TextField(verbose_name=tr('Reason'))
    status = models.CharField(max_length=12, default='pending', choices=[
        ('pending', tr('Pending approval')),
        ('approved', tr('Approved')),
        ('rejected', tr('Rejected')),
        ('superseded', tr('Superseded by a time adjustment')),
    ])
    review_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portal_extensionrequest'
        constraints = [models.UniqueConstraint(fields=['request'], condition=Q(status='pending'), name='one_pending_extension')]
