from django.conf import settings
from django.db import models


class RegisteredResource(models.Model):
    accounting = models.CharField(max_length=10, default='task')
    volume_kind = models.CharField(max_length=10, null=True)
    allocation_group = models.CharField(max_length=100, null=True)
    allocation_field = models.CharField(max_length=100, null=True)
    per_allocation = models.BooleanField(default=False)
    minimum = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    maximum = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    step = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    key = models.CharField(max_length=100, primary_key=True)
    unit = models.CharField(max_length=40)
    label = models.JSONField(default=dict)
    scope = models.CharField(max_length=10, default='cluster')
    integral = models.BooleanField(default=False)
    requestable = models.BooleanField(default=True)

    @property
    def has_member_quota(self):
        return self.accounting != "volume" or self.volume_kind == "work"

    class Meta:
        db_table = 'portal_registeredresource'

class NodeResource(models.Model):
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.CASCADE, related_name='resources')
    resource = models.ForeignKey('platform_quotas.RegisteredResource', on_delete=models.PROTECT)
    total = models.DecimalField(max_digits=24, decimal_places=6)
    used = models.DecimalField(max_digits=24, decimal_places=6, default=0)
    observed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'portal_noderesource'
        constraints = [models.UniqueConstraint(fields=['node', 'resource'], name='unique_node_resource')]

class ResourceQuota(models.Model):
    per_allocation_limit = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    group = models.ForeignKey('platform_accounts.UserGroup', on_delete=models.CASCADE, related_name='resource_quotas')
    resource = models.ForeignKey('platform_quotas.RegisteredResource', on_delete=models.CASCADE)
    limit = models.DecimalField(max_digits=24, decimal_places=6, null=True)

    class Meta:
        db_table = 'portal_resourcequota'
        constraints = [models.UniqueConstraint(fields=['group', 'resource'], name='unique_group_resource_quota')]

class ResourceReservation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT)
    resource = models.ForeignKey('platform_quotas.RegisteredResource', on_delete=models.PROTECT)
    subject = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=24, decimal_places=6)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portal_resourcereservation'
        constraints = [models.UniqueConstraint(fields=['owner', 'subject', 'resource'], name='unique_subject_resource')]
