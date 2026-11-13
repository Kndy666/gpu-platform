import fcntl
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = 'Deliver configured notifications independently of workload reconciliation'

    def handle(self, *args, **options):
        from portal.notifications import collect_expiry_reminders, deliver_one
        stopping = []
        signal.signal(signal.SIGTERM, lambda *args: stopping.append(True))
        signal.signal(signal.SIGINT, lambda *args: stopping.append(True))
        with open(settings.DATA_DIR / 'notifications.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            counter = 0
            while not stopping:
                close_old_connections()
                try:
                    if counter % 30 == 0:
                        collect_expiry_reminders()
                    deliver_one()
                except Exception as exc:
                    self.stderr.write('Notification retry: ' + type(exc).__name__)
                counter += 1
                time.sleep(1)
