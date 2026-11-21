from django.apps import AppConfig
from django.db.backends.signals import connection_created


def sqlite_setup(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        with connection.cursor() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA foreign_keys=ON")


class ControlConfig(AppConfig):
    name = "gpu_platform_control.core"
    label = "platform_core"
    verbose_name = "Resource control"

    def ready(self):
        connection_created.connect(sqlite_setup, dispatch_uid="sqlite-platform-wal")
        from . import signals  # noqa: F401
