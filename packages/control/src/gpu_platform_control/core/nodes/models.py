from django.utils.translation import gettext_lazy as tr
from django.db import models
from django.core.exceptions import ValidationError


def default_node_id():
    from gpu_platform_control.slots.configuration import cluster_config
    nodes = [(name, host) for name, host in cluster_config().hosts.items() if 'node' in host.roles]
    if not nodes:
        raise ValidationError(tr("No servers are configured. Ask an administrator to add one."))
    name, host = next(((name, host) for name, host in nodes if 'control' in host.roles), nodes[0])
    return ComputeNode.objects.get_or_create(name=name, defaults={'architecture': host.architecture})[0].pk


class ComputeNode(models.Model):
    name = models.SlugField(max_length=63, unique=True, verbose_name=tr("Server name"))
    architecture = models.CharField(max_length=20, choices=[('arm64', 'ARM64'), ('amd64', 'AMD64')], default='amd64')
    enabled = models.BooleanField(default=True, verbose_name=tr('Enable reporting'))
    maintenance = models.BooleanField(default=False, verbose_name=tr('Maintenance mode'))
    state = models.CharField(max_length=16, default='unknown', choices=[('unknown', tr('Disconnected')), ('online', tr('Online')), ('offline', tr('Offline'))])
    last_seen = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    protocol_version = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'portal_computenode'

class StoragePool(models.Model):
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, default=default_node_id)
    name = models.SlugField(verbose_name=tr('Name'))
    capacity_gib = models.PositiveBigIntegerField(default=0, verbose_name=tr('Total capacity GiB'))
    free_gib = models.PositiveBigIntegerField(default=0, verbose_name=tr('Free GiB'))
    reserved_gib = models.PositiveBigIntegerField(default=0, verbose_name=tr('Reserved GiB'))
    buffer_percent = models.PositiveIntegerField(default=5, verbose_name=tr("Storage reserve (%)"))
    healthy = models.BooleanField(default=False, verbose_name=tr('Healthy'))
    observed_at = models.DateTimeField(null=True, blank=True, verbose_name=tr('Updated'))

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'portal_storagepool'
        verbose_name = tr('Storage pool')
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['node', 'name'], name='node_pool_name')]

class SystemObservation(models.Model):
    node = models.ForeignKey('platform_nodes.ComputeNode', on_delete=models.PROTECT, default=default_node_id)
    name = models.CharField(max_length=80, verbose_name=tr('Name'))
    data = models.JSONField(default=dict, verbose_name=tr("Latest readings"))
    observed_at = models.DateTimeField(auto_now=True, verbose_name=tr('Updated'))

    class Meta:
        db_table = 'portal_systemobservation'
        verbose_name = tr("Resource readings")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['node', 'name'], name='node_observation')]
