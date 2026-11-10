import fcntl
import logging
import signal
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from portal.configuration import config
from portal.reconcile import Reconciler
from portal.models import ComputeNode, default_node_id

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
            default_node_id()
            while not stop:
                from concurrent.futures import ThreadPoolExecutor
                from django.db import close_old_connections
                def sync(node):
                    close_old_connections()
                    try:
                        Reconciler(node=node).tick()
                    except Exception:
                        logging.getLogger(__name__).exception('节点同步失败 %s；下一轮重试', node.name)
                    finally:
                        close_old_connections()
                nodes = list(ComputeNode.objects.filter(enabled=True))
                with ThreadPoolExecutor(max_workers=min(8, max(1, len(nodes)))) as executor:
                    list(executor.map(sync, nodes))
                if options['once']:
                    break
                for _ in range(config()['platform']['reconcile_seconds']):
                    if stop: break
                    time.sleep(1)
