from django.apps import AppConfig


class RequestsConfig(AppConfig):
    name = 'gpu_platform_control.core.requests'
    label = 'platform_requests'
    default_auto_field = 'django.db.models.BigAutoField'
