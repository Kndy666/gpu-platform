"""Publish task login connections through controller-owned TCP listeners."""

import selectors
import socket
import socketserver
import threading
from typing import cast
from pydantic import Field

from gpu_platform_contracts.base import Contract
from gpu_platform_contracts.handoffs import LoginAddress
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.entry import Entry


class Options(Contract):
    bind_host: str = Field(default="0.0.0.0", min_length=1,
        description="Controller interface on which task login ports listen.")
    public_host: str = Field(default="localhost", min_length=1,
        description="Public hostname returned in each task login address.")


class Forwarder(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    target: tuple[str, int]

    def __init__(self, *args, **kwargs):
        self.connections: set[socket.socket] = set()
        self.connection_lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__(*args, **kwargs)

    def close_connections(self):
        self.stopping.set()
        with self.connection_lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class Handler(socketserver.BaseRequestHandler):
    def setup(self):
        server = cast(Forwarder, self.server)
        with server.connection_lock:
            server.connections.add(self.request)

    def finish(self):
        server = cast(Forwarder, self.server)
        with server.connection_lock:
            server.connections.discard(self.request)

    def handle(self):
        server = cast(Forwarder, self.server)
        with socket.create_connection(server.target, timeout=10) as upstream:
            upstream.settimeout(None)
            with selectors.DefaultSelector() as selector:
                selector.register(self.request, selectors.EVENT_READ, upstream)
                selector.register(upstream, selectors.EVENT_READ, self.request)
                while selector.get_map() and not server.stopping.is_set():
                    for key, _ in selector.select(timeout=0.2):
                        try:
                            data = cast(socket.socket, key.fileobj).recv(65536)
                            if data:
                                key.data.sendall(data)
                            else:
                                selector.unregister(key.fileobj)
                                key.data.shutdown(socket.SHUT_WR)
                        except OSError:
                            return


class Module(Entry):
    options_model = Options
    descriptor = ModuleDescriptor(
        name="controller-forward", slot="entry", version="0.4.0", side="both", accepts={"port": {"tcp"}},
        parameters=Options.model_json_schema(),
    )

    def __init__(self, context):
        super().__init__(context)
        self.listeners = {}
        self.lock = threading.RLock()

    def open(self, task_id, username, connection, public_port):
        if public_port is None or not 1 <= public_port <= 65535:
            raise ValueError("entry.public_port_required")
        target = (connection.host, connection.port)
        with self.lock:
            current = self.listeners.get(task_id)
            if (
                current
                and current[0].target == target
                and current[1].port == public_port
                and current[1].username == username
            ):
                return current[1]
            self.close(task_id)
            listener = Forwarder((self.context.options.get("bind_host", "0.0.0.0"), public_port), Handler)
            listener.target = target
            address = LoginAddress(
                host=self.context.options.get("public_host", "localhost"),
                port=public_port,
                username=username,
                task_id=task_id,
            )
            thread = threading.Thread(
                target=listener.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
            )
            thread.start()
            self.listeners[task_id] = (listener, address, thread)
            return address

    def close(self, task_id):
        with self.lock:
            current = self.listeners.pop(task_id, None)
            if current:
                current[0].close_connections()
                current[0].shutdown()
                current[0].server_close()
                current[2].join(timeout=2)

    def address(self, task_id):
        with self.lock:
            current = self.listeners.get(task_id)
            return current[1] if current else None
