from django.apps import AppConfig


class QuotasConfig(AppConfig):
    name = 'gpu_platform_control.core.quotas'
    label = 'platform_quotas'
    default_auto_field = 'django.db.models.BigAutoField'
