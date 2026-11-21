from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = 'gpu_platform_control.core.audit'
    label = 'platform_audit'
    default_auto_field = 'django.db.models.BigAutoField'
