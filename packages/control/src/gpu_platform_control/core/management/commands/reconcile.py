import fcntl
import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from gpu_platform_control.core.nodes.models import ComputeNode, SystemObservation
from gpu_platform_control.core.metrics_history import MetricsArchive
from gpu_platform_control.loop.metrics_history import HistoryRecorder
from gpu_platform_control.loop.reconcile import Reconciler
from gpu_platform_control.loop.transfers import Transfers
from gpu_platform_control.slots.gateway import Gateway


class Command(BaseCommand):
    help = "Reconcile typed node reports and dispatch approved work"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        with open(settings.DATA_DIR / "reconciler.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError("Another reconciler is active") from exc
            stop = []
            signal.signal(signal.SIGTERM, lambda *args: stop.append(True))
            signal.signal(signal.SIGINT, lambda *args: stop.append(True))
            gateway = Gateway()
            transfers = Transfers(gateway)
            transfers.recover()

            def sync(node):
                close_old_connections()
                try:
                    Reconciler(node, gateway, metrics_history=history.submit).tick()
                except Exception:
                    logging.getLogger(__name__).exception("Node reconciliation failed: %s", node.name)
                finally:
                    close_old_connections()

            with (
                HistoryRecorder(MetricsArchive.configured()) as history,
                ThreadPoolExecutor(max_workers=8) as executor,
                ThreadPoolExecutor(max_workers=1) as transfer_executor,
            ):
                transfer_future = None
                while not stop:
                    list(executor.map(sync, ComputeNode.objects.filter(enabled=True)))
                    for node in ComputeNode.objects.filter(enabled=True):
                        SystemObservation.objects.update_or_create(
                            node=node, name="reconciler", defaults={"data": {"active": True}}
                        )
                    if transfer_future is None or transfer_future.done():
                        transfer_future = transfer_executor.submit(transfers.tick)
                    if options["once"]:
                        break
                    for _ in range(10):
                        if stop:
                            break
                        time.sleep(1)
