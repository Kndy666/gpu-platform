"""Local command and stream endpoint for the controller's links process."""

import socket
import socketserver
import struct
from pathlib import Path

from django.db import close_old_connections

from gpu_platform_contracts.messages import Command, Reply, encode_message, read_message
from gpu_platform_contracts.texts import Message

from gpu_platform_control.core.nodes.models import ComputeNode


def chunks(stream):
    while chunk := stream.read(1024 * 1024):
        yield chunk


class Handler(socketserver.StreamRequestHandler):
    server: "Server"

    def handle(self):
        self.request.settimeout(300)
        identity = "invalid"
        sent = False
        close_old_connections()
        try:
            payload = read_message(self.rfile)
            node = self.server.resolve_node(payload["node"])
            transport = self.server.gateway.module(node, "transport")
            endpoint = self.server.gateway.endpoint(node)
            if "command" in payload:
                command = Command.model_validate(payload["command"])
                identity = command.id
                self.wfile.write(encode_message(transport.request(endpoint, command)))
            else:
                identity = payload["stream_id"]
                if payload["direction"] == "upload":
                    size = transport.send_stream(endpoint, identity, chunks(self.rfile))
                    self.wfile.write(encode_message(Reply(id=identity, ok=True, data={"size_bytes": size})))
                elif payload["direction"] == "download":
                    source = iter(transport.receive_stream(endpoint, identity))
                    first = next(source, b"")
                    self.wfile.write(encode_message(Reply(id=identity, ok=True)))
                    self.wfile.flush()
                    sent = True
                    if first:
                        self.wfile.write(struct.pack("!I", len(first)) + first)
                    for chunk in source:
                        if chunk:
                            self.wfile.write(struct.pack("!I", len(chunk)) + chunk)
                    self.wfile.write(struct.pack("!I", 0))
                else:
                    raise ValueError("invalid stream direction")
        except Exception:
            if not sent:
                self.wfile.write(
                    encode_message(Reply(id=identity, ok=False, error=Message(code="transport.unavailable")))
                )
        finally:
            close_old_connections()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, gateway, resolve_node=None):
        self.path, self.gateway = path, gateway
        self.resolve_node = resolve_node or (lambda name: ComputeNode.objects.get(name=name))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with socket.socket(socket.AF_UNIX) as connection:
                try:
                    connection.connect(str(path))
                except ConnectionRefusedError:
                    path.unlink()
                else:
                    raise RuntimeError("links socket is already active")
        super().__init__(str(path), Handler)
        path.chmod(0o660)

    def server_close(self):
        super().server_close()
        self.path.unlink(missing_ok=True)
