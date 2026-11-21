from django.apps import AppConfig


class EventsConfig(AppConfig):
    name = 'gpu_platform_control.core.events'
    label = 'platform_events'
    default_auto_field = 'django.db.models.BigAutoField'
