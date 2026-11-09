import fcntl
import signal
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from portal.configuration import config
from portal.reconcile import Reconciler

class Command(BaseCommand):
    help = 'Run the single-instance reconciler, or one tick with --once'
    def add_arguments(self, parser): parser.add_argument('--once', action='store_true')
    def handle(self, *args, **options):
        with open(settings.DATA_DIR / 'reconciler.lock', 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise CommandError('Another reconciler is active') from e
            stop = []
            signal.signal(signal.SIGTERM, lambda *a: stop.append(True))
            signal.signal(signal.SIGINT, lambda *a: stop.append(True))
            reconciler = Reconciler()
            while not stop:
                reconciler.tick()
                if options['once']:
                    break
                for _ in range(config()['platform']['reconcile_seconds']):
                    if stop: break
                    time.sleep(1)
