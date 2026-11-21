from django.apps import AppConfig


class SyncConfig(AppConfig):
    name = 'gpu_platform_control.core.sync'
    label = 'platform_sync'
    default_auto_field = 'django.db.models.BigAutoField'
