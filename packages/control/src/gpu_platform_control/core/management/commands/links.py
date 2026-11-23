import signal
import threading
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from gpu_platform_control.loop.links import Links
from gpu_platform_control.slots.gateway import Gateway
from gpu_platform_control.loop.links_proxy import Server
from gpu_platform_control.loop.peer_links import PeerLinks


class Command(BaseCommand):
    help = "Maintain login routes through configured entry modules"

    def handle(self, *args, **options):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *args: stop.set())
        signal.signal(signal.SIGINT, lambda *args: stop.set())
        links = Links(Gateway())
        peers = PeerLinks(links.gateway)
        server = Server(
            Path(links.gateway.configuration.cluster.state_directory) / "links.sock", links.gateway
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def maintain_peer_links():
            try:
                while not stop.is_set():
                    close_old_connections()
                    try:
                        peers.tick()
                    except Exception as exc:
                        self.stderr.write("Workspace SSH retry: " + type(exc).__name__)
                    stop.wait(1)
            finally:
                close_old_connections()

        peer_thread = threading.Thread(target=maintain_peer_links, daemon=True)
        peer_thread.start()
        try:
            while not stop.is_set():
                try:
                    links.tick()
                except Exception as exc:
                    self.stderr.write("Login route retry: " + type(exc).__name__)
                stop.wait(1)
        finally:
            stop.set()
            peer_thread.join(timeout=5)
            peers.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            links.close()
