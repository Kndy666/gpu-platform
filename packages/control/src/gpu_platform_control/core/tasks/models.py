from django.utils.translation import gettext_lazy as tr
from django.db import models


class Workspace(models.Model):
    allocations = models.JSONField(default=dict, blank=True)
    node_port = models.PositiveIntegerField(null=True, blank=True)
    connection = models.JSONField(default=dict, blank=True)
    dispatch_payload = models.JSONField(default=dict, blank=True)
    request = models.OneToOneField('platform_requests.ResourceRequest', on_delete=models.PROTECT, primary_key=True, verbose_name=tr('Resource request'))
    ssh_port = models.PositiveIntegerField(null=True, blank=True, unique=True, verbose_name=tr('SSH port'))
    started_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Started at'))
    dispatch_started_at = models.DateTimeField(null=True, blank=True)
    expire_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Ends at'))
    exit_code = models.IntegerField(null=True, blank=True, verbose_name=tr('Exit code'))
    error = models.TextField(blank=True, verbose_name=tr('Error'))
    stop_requested = models.BooleanField(default=False, verbose_name=tr('Stop requested'))
    cleanup_complete = models.BooleanField(default=False, verbose_name=tr('Resources released'))

    class Meta:
        db_table = 'portal_workspace'
        verbose_name = tr('Task instance')
        verbose_name_plural = verbose_name
