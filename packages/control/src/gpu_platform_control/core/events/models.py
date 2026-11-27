from django.utils.translation import gettext_lazy as tr
from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationSettings(models.Model):
    receivers = models.JSONField(default=dict, blank=True)
    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False, verbose_name=tr('Enable notifications'))
    events = models.JSONField(default=list, blank=True)
    reminder_minutes = models.PositiveIntegerField(default=30, verbose_name=tr('Reminder lead time (minutes)'))

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(singleton=1)[0]

    class Meta:
        db_table = 'portal_notificationsettings'

class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    channel = models.CharField(max_length=100, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    event_key = models.CharField(max_length=255, unique=True)
    event = models.CharField(max_length=40)
    title = models.CharField(max_length=100)
    content = models.TextField()
    state = models.CharField(max_length=12, default='pending', choices=[
        ('pending', tr('Waiting for delivery')), ('sending', tr('Sending')),
        ('sent', tr('Delivered to channel')), ('failed', tr('Delivery failed')),
        ('canceled', tr('Canceled')), ('revoked', tr('Withdrawn')), ('skipped', tr('Skipped')),
    ])
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portal_notification'
        indexes = [models.Index(fields=['state', 'next_attempt_at'], name='notification_delivery_idx')]

class EventReceipt(models.Model):
    source = models.CharField(max_length=255)
    event_id = models.UUIDField()
    audit = models.OneToOneField('platform_audit.AuditLog', on_delete=models.PROTECT)

    class Meta:
        db_table = 'platform_eventreceipt'
        constraints = [models.UniqueConstraint(fields=['source', 'event_id'], name='unique_module_event')]
