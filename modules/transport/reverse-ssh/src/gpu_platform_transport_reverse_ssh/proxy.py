"""Access controller-owned SSH channels through a local Unix socket."""

import json
import socket
import struct
from contextlib import contextmanager

from gpu_platform_contracts.messages import MAX_MESSAGE_BYTES, Reply, read_message


def header(value):
    frame = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    if len(frame) > MAX_MESSAGE_BYTES:
        raise ValueError("transport.message_too_large")
    return frame


class Proxy:
    def __init__(self, path, node_id):
        self.path, self.node_id = path, node_id

    @contextmanager
    def connect(self):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(300)
            connection.connect(self.path)
            yield connection

    def request(self, command):
        with self.connect() as connection:
            connection.sendall(header({"node": self.node_id, "command": command.model_dump(mode="json")}))
            with connection.makefile("rb") as stream:
                reply = Reply.model_validate(read_message(stream))
            if reply.id != command.id:
                raise ConnectionError("transport.invalid_reply_identity")
            return reply

    def send_stream(self, stream_id, chunks):
        with self.connect() as connection:
            connection.sendall(header({"node": self.node_id, "stream_id": stream_id, "direction": "upload"}))
            size = 0
            for chunk in chunks:
                connection.sendall(chunk)
                size += len(chunk)
            connection.shutdown(socket.SHUT_WR)
            with connection.makefile("rb") as stream:
                reply = Reply.model_validate(read_message(stream))
            if not reply.ok or reply.id != stream_id or reply.data.get("size_bytes") != size:
                raise ConnectionError("transport.upload_failed")
            return size

    def receive_stream(self, stream_id):
        with self.connect() as connection:
            connection.sendall(
                header({"node": self.node_id, "stream_id": stream_id, "direction": "download"})
            )
            with connection.makefile("rb") as stream:
                reply = Reply.model_validate(read_message(stream))
                if not reply.ok or reply.id != stream_id:
                    raise ConnectionError("transport.download_failed")
                while True:
                    length = stream.read(4)
                    if len(length) != 4:
                        raise ConnectionError("transport.incomplete_stream")
                    size = struct.unpack("!I", length)[0]
                    if size == 0:
                        break
                    if size > 1024 * 1024:
                        raise ConnectionError("transport.invalid_chunk")
                    chunk = stream.read(size)
                    if len(chunk) != size:
                        raise ConnectionError("transport.incomplete_stream")
                    yield chunk
