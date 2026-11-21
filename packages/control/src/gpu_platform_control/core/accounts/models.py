from django.utils.translation import gettext_lazy as tr
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator

GROUP_QUOTA_FIELDS = ('concurrent_tasks', 'interactive_hours')
APPROVAL_CHOICES = [('manual', tr('Manual approval')), ('automatic', tr('Automatic approval'))]


class UserGroup(models.Model):
    name = models.CharField(max_length=80, unique=True, verbose_name=tr('Group name'))
    is_default = models.BooleanField(default=False, verbose_name=tr('Default group'))
    unlimited = models.BooleanField(default=False, verbose_name=tr('Unlimited resources'))
    home_size_gib = models.PositiveIntegerField(default=50, validators=[MinValueValidator(1)], verbose_name=tr('Home volume GiB'))
    concurrent_tasks = models.PositiveIntegerField(default=1, null=True, blank=True, verbose_name=tr('Concurrent tasks'))
    interactive_hours = models.PositiveIntegerField(default=120, null=True, blank=True, verbose_name=tr('Maximum environment duration (hours)'))
    extension_limit = models.PositiveIntegerField(default=1, verbose_name=tr('Extensions per environment'))
    approval_mode = models.CharField(max_length=12, choices=APPROVAL_CHOICES, default='automatic', verbose_name=tr('Approval policy'))

    def clean(self):
        super().clean()
        if self.unlimited:
            for name in GROUP_QUOTA_FIELDS:
                setattr(self, name, None)
        else:
            errors = {name: tr('Set a resource limit.') for name in GROUP_QUOTA_FIELDS if getattr(self, name) is None}
            if errors:
                raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        if self.unlimited and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | set(GROUP_QUOTA_FIELDS)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'portal_usergroup'
        verbose_name = tr('User groups')
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['is_default'], condition=Q(is_default=True), name='one_default_user_group')]

class UserProfile(models.Model):
    notification_receivers = models.JSONField(default=dict, blank=True)
    language = models.CharField(max_length=10, blank=True, default='', choices=[('en', 'English'), ('zh-hans', tr('Simplified Chinese'))])
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile', verbose_name=tr('User'))
    group = models.ForeignKey('platform_accounts.UserGroup', on_delete=models.PROTECT, related_name='members', verbose_name=tr('User groups'))
    uid = models.PositiveIntegerField(default=1000)
    gid = models.PositiveIntegerField(default=1000)
    home_size_gib = models.PositiveIntegerField(default=50, verbose_name=tr('Home volume GiB'))
    resource_limits = models.JSONField(default=dict, verbose_name=tr('Member resource quotas'))
    extension_limit = models.PositiveIntegerField(default=1, verbose_name=tr('Extensions per environment'))
    approval_mode = models.CharField(max_length=12, choices=APPROVAL_CHOICES, default='automatic', verbose_name=tr('Approval policy'))

    class Meta:
        db_table = 'portal_userprofile'
        verbose_name = tr('Users and home volumes')
        verbose_name_plural = verbose_name

class SSHKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name=tr('User'))
    name = models.CharField(max_length=100, verbose_name=tr('Name'))
    public_key = models.TextField(verbose_name=tr('Public key'))
    fingerprint = models.CharField(max_length=100, verbose_name=tr('Fingerprint'))
    enabled = models.BooleanField(default=True, verbose_name=tr('Enable'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=tr('Created'))

    class Meta:
        db_table = 'portal_sshkey'
        verbose_name = tr('SSH keys')
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=['user', 'fingerprint'], name='unique_user_key')]

class ExternalIdentity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='external_identities')
    provider = models.CharField(max_length=128)
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portal_externalidentity'
        constraints = [models.UniqueConstraint(fields=['provider', 'subject'], name='unique_provider_subject')]
