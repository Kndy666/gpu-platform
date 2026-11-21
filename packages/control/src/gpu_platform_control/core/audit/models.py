from django.utils.translation import gettext_lazy as tr
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=tr('Actor'))
    action = models.CharField(max_length=80, verbose_name=tr('Actions'))
    target = models.CharField(max_length=100, verbose_name=tr('Target'))
    detail = models.JSONField(default=dict, verbose_name=tr('Details'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=tr('Created'))

    class Meta:
        db_table = 'portal_auditlog'
        verbose_name = tr('Audit log')
        verbose_name_plural = verbose_name
