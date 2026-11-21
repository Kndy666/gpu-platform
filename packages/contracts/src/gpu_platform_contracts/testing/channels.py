"""Isolated protocol peers for transport implementations and their adapters."""

import shlex
import socket
import socketserver
import subprocess
import threading
from contextlib import contextmanager

from ..messages import Reply, encode_message, read_message


@contextmanager
def running(server):
    with server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=2)


@contextmanager
def protocol_peer(path):
    data = {}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = read_message(self.rfile)
            if request.get("channel") == "stream":
                identity = request["stream_id"]
                if request["direction"] == "upload":
                    data[identity] = self.rfile.read()
                    self.wfile.write(
                        encode_message(Reply(id=identity, ok=True, data={"size_bytes": len(data[identity])}))
                    )
                else:
                    self.wfile.write(
                        encode_message(Reply(id=identity, ok=True, data={"size_bytes": len(data[identity])}))
                    )
                    self.wfile.write(data[identity])
            else:
                self.wfile.write(encode_message(Reply(id=request["id"], ok=True)))

    with running(socketserver.ThreadingUnixStreamServer(str(path), Handler)):
        yield str(path)


@contextmanager
def echo_peer():
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while data := self.request.recv(65536):
                self.request.sendall(data)

    with running(socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)) as server:
        yield server.server_address[1]


class ForwardProcess:
    """Emulate the injected SSH subprocess's TCP forwarding using real sockets."""

    def __init__(self, specification):
        host, port, target, remote = specification.split(":")

        class Forward(socketserver.BaseRequestHandler):
            def handle(self):
                with socket.create_connection((target, int(remote)), timeout=2) as upstream:
                    while data := self.request.recv(65536):
                        upstream.sendall(data)
                        remaining = len(data)
                        while remaining:
                            received = upstream.recv(remaining)
                            if not received:
                                return
                            self.request.sendall(received)
                            remaining -= len(received)

        self.server = socketserver.ThreadingTCPServer((host, int(port)), Forward)
        self.worker = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.worker.start()
        self.ended = False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.ended = True

    def wait(self, timeout=None):
        return 0


def local_ssh_process(arguments, **options):
    if "-L" in arguments:
        return ForwardProcess(arguments[arguments.index("-L") + 1])
    return subprocess.Popen(shlex.split(arguments[-1]), **options)
