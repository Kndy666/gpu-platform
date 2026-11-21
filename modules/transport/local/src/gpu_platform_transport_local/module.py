import socket
from contextlib import contextmanager
from pydantic import Field

from gpu_platform_contracts.base import Contract
from gpu_platform_contracts.handoffs import PortConnection
from gpu_platform_contracts.messages import Command, Operation, Reply
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.transport import Transport

from .wire import connect, encode_message, exchange, read_message


class Options(Contract):
    socket_path: str | None = Field(default=None, min_length=1,
        description="Unix socket path; defaults to the endpoint or node.sock under the state directory.")
    port_host: str = Field(default="127.0.0.1", min_length=1,
        description="Host used when opening a connection to a local node port.")


class Module(Transport):
    options_model = Options
    descriptor = ModuleDescriptor(
        name="local",
        slot="transport",
        version="0.4.0",
        side="both",
        provides={"port": {"tcp"}, "stream": {"bytes"}},
        parameters=Options.model_json_schema(),
    )

    def path(self, endpoint):
        return self.context.options.get("socket_path") or endpoint or str(self.context.state_directory / "node.sock")

    def request(self, endpoint, command):
        reply = Reply.model_validate(exchange(self.path(endpoint), command.model_dump(mode="json")))
        if reply.id != command.id:
            raise ConnectionError("invalid_reply_identity")
        return reply

    def online(self, endpoint):
        try:
            return self.request(endpoint, Command(operation=Operation.CAPABILITIES)).ok
        except (OSError, ValueError):
            return False

    def send_stream(self, endpoint, stream_id, chunks):
        with connect(self.path(endpoint)) as connection:
            connection.sendall(
                encode_message({"channel": "stream", "direction": "upload", "stream_id": stream_id})
            )
            transferred = 0
            for chunk in chunks:
                connection.sendall(chunk)
                transferred += len(chunk)
            connection.shutdown(socket.SHUT_WR)
            with connection.makefile("rb") as stream:
                reply = Reply.model_validate(read_message(stream))
            if not reply.ok or reply.id != stream_id or reply.data.get("size_bytes") != transferred:
                raise ConnectionError("stream_upload_failed")
            return transferred

    def receive_stream(self, endpoint, stream_id):
        with connect(self.path(endpoint)) as connection:
            connection.sendall(
                encode_message({"channel": "stream", "direction": "download", "stream_id": stream_id})
            )
            with connection.makefile("rb") as stream:
                reply = Reply.model_validate(read_message(stream))
                if not reply.ok or reply.id != stream_id:
                    raise ConnectionError("stream_download_failed")
                transferred = 0
                while chunk := stream.read(1024 * 1024):
                    transferred += len(chunk)
                    yield chunk
                if transferred != reply.data.get("size_bytes"):
                    raise ConnectionError("stream_download_incomplete")

    @contextmanager
    def open_port(self, endpoint, port):
        yield PortConnection(host=self.context.options.get("port_host", "127.0.0.1"), port=port)
