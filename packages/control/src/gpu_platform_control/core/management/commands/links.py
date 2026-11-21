import signal
import time
import threading
from pathlib import Path
from django.core.management.base import BaseCommand
from gpu_platform_control.loop.links import Links
from gpu_platform_control.slots.gateway import Gateway
from gpu_platform_control.loop.links_proxy import Server


class Command(BaseCommand):
    help = "Maintain login routes through configured entry modules"

    def handle(self, *args, **options):
        stop = []
        signal.signal(signal.SIGTERM, lambda *args: stop.append(True))
        signal.signal(signal.SIGINT, lambda *args: stop.append(True))
        links = Links(Gateway())
        server = Server(
            Path(links.gateway.configuration.cluster.state_directory) / "links.sock", links.gateway
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            while not stop:
                try:
                    links.tick()
                except Exception as exc:
                    self.stderr.write("Login route retry: " + type(exc).__name__)
                time.sleep(1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            links.close()
