from django.apps import AppConfig


class TasksConfig(AppConfig):
    name = 'gpu_platform_control.core.tasks'
    label = 'platform_tasks'
    default_auto_field = 'django.db.models.BigAutoField'
