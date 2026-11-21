"""Node channels carried by an existing reverse SSH endpoint."""

import json
import os
import shlex
import socket
import subprocess
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from pydantic import Field

from gpu_platform_contracts.base import Contract
from gpu_platform_contracts.handoffs import PortConnection
from gpu_platform_contracts.messages import (
    Command,
    Operation,
    Reply,
    encode_message,
    read_message,
)
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.transport import Transport

from .proxy import Proxy

# The peer requires only the host Python standard library. The node service owns
# the Unix socket, so SSH carries both control and data without a second daemon.
BRIDGE = """import os,socket,sys,threading
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.connect(sys.argv[1])
def upload():
    try:
        while True:
            data=os.read(0,65536)
            if not data:break
            s.sendall(data)
    finally:s.shutdown(socket.SHUT_WR)
threading.Thread(target=upload,daemon=True).start()
while True:
    data=s.recv(65536)
    if not data:break
    sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
s.close()
"""


class Options(Contract):
    host: str = Field(default="127.0.0.1", min_length=1, pattern=r"^[^-\s][^\s]*$",
        description="SSH endpoint hostname, usually the existing reverse tunnel listener.")
    ssh_user: str = Field(default="gpu-platform", min_length=1, pattern=r"^[a-z_][a-z0-9_-]*[$]?$",
        description="Host account that can access the node socket.")
    ssh_port: int = Field(default=22, ge=1, le=65535,
        description="SSH endpoint TCP port.")
    identity: str | None = Field(default=None, min_length=1,
        description="Controller SSH private key path, absolute or relative to key_directory.")
    key_directory: str | None = Field(default=None, min_length=1,
        description="Directory containing keys and known_hosts; defaults to ssh-keys under state_directory.")
    socket_path: str | None = Field(default=None, min_length=1,
        description="Node Unix socket path; defaults to node.sock under state_directory.")
    proxy_socket: str | None = Field(default=None, min_length=1,
        description="Controller links proxy socket; defaults to PLATFORM_TRANSPORT_PROXY when set.")


class Module(Transport):
    options_model = Options
    @classmethod
    def check_host(cls, context):
        from .install import check_host
        return check_host(context)

    @classmethod
    def install_host(cls, context):
        from .install import install_host
        install_host(context)

    descriptor = ModuleDescriptor(
        name="reverse-ssh",
        slot="transport",
        version="0.4.0",
        side="both",
        provides={"port": {"tcp"}, "stream": {"bytes"}},
        parameters=Options.model_json_schema(),
    )

    def __init__(self, context, popen=subprocess.Popen):
        super().__init__(context)
        self.popen = popen
        proxy_path = context.options.get("proxy_socket") or os.environ.get("PLATFORM_TRANSPORT_PROXY")
        self.proxy = Proxy(proxy_path, context.node_id) if proxy_path else None

    @classmethod
    def compose(cls, context):
        if "control" not in getattr(context, "host", {}).get("roles", []):
            return {}
        directory = str(context.state_directory)
        keys = str(context.options.get("key_directory") or context.state_directory / "ssh-keys")
        return {
            "services": {
                "links": {
                    "volumes": [f"{keys}:{keys}:ro"],
                },
                "loop": {"environment": {"PLATFORM_TRANSPORT_PROXY": directory + "/links.sock"}},
                "web": {"environment": {"PLATFORM_TRANSPORT_PROXY": directory + "/links.sock"}},
            }
        }

    def arguments(self, endpoint, *, multiplex=True):
        options = self.context.options
        host = options.get("host", "127.0.0.1")
        user = options.get("ssh_user", "gpu-platform")
        port = int(options.get("ssh_port", 22))
        if not 1 <= port <= 65535 or host.startswith("-") or user.startswith("-"):
            raise ValueError("invalid_ssh_endpoint")
        directory = Path(options.get("key_directory") or self.context.state_directory / "ssh-keys")
        if not options.get("identity"):
            raise ValueError("transport.controller_keys_required")
        identity = Path(options["identity"])
        if not identity.is_absolute():
            identity = directory / identity
        if multiplex:
            control = self.context.state_directory / "ssh-control"
            control.mkdir(mode=0o700, parents=True, exist_ok=True)
            sharing = [
                "-o", "ControlMaster=auto",
                "-o", "ControlPersist=60",
                "-o", "ControlPath=" + str(control / "%C"),
            ]
        else:
            # A persistent master keeps its forwards after the requesting client exits.
            sharing = ["-S", "none", "-o", "ControlMaster=no"]
        return [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(directory / "known_hosts"),
            *sharing,
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(port),
            "-i",
            str(identity),
            f"{user}@{host}",
        ]

    def channel(self, endpoint):
        command = shlex.join(
            [
                "python3",
                "-c",
                BRIDGE,
                self.context.options.get("socket_path") or str(self.context.state_directory / "node.sock"),
            ]
        )
        return self.popen(
            [*self.arguments(endpoint), command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    @contextmanager
    def session(self, endpoint):
        process = self.channel(endpoint)
        try:
            yield process
        finally:
            for stream in (process.stdin, process.stdout):
                if stream and not stream.closed:
                    stream.close()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def request(self, endpoint, command):
        if self.proxy:
            return self.proxy.request(command)
        with self.session(endpoint) as process:
            timer = threading.Timer(50, process.terminate)
            timer.daemon = True
            timer.start()
            try:
                process.stdin.write(encode_message(command))
                process.stdin.close()
                reply = Reply.model_validate(read_message(process.stdout))
            finally:
                timer.cancel()
            if reply.id != command.id:
                raise ConnectionError("transport.invalid_reply")
            return reply

    def online(self, endpoint):
        try:
            return self.request(endpoint, Command(operation=Operation.CAPABILITIES)).ok
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def send_stream(self, endpoint, stream_id, chunks):
        if self.proxy:
            return self.proxy.send_stream(stream_id, chunks)
        with self.session(endpoint) as process:
            header = {"channel": "stream", "direction": "upload", "stream_id": stream_id}
            process.stdin.write(json.dumps(header).encode() + b"\n")
            count = 0
            for chunk in chunks:
                process.stdin.write(chunk)
                count += len(chunk)
            process.stdin.close()
            reply = Reply.model_validate(read_message(process.stdout))
            if not reply.ok or reply.id != stream_id or reply.data.get("size_bytes") != count:
                raise ConnectionError("transport.upload_failed")
            return count

    def receive_stream(self, endpoint, stream_id):
        if self.proxy:
            yield from self.proxy.receive_stream(stream_id)
            return
        with self.session(endpoint) as process:
            header = {"channel": "stream", "direction": "download", "stream_id": stream_id}
            process.stdin.write(json.dumps(header).encode() + b"\n")
            process.stdin.close()
            reply = Reply.model_validate(read_message(process.stdout))
            if not reply.ok or reply.id != stream_id:
                raise ConnectionError("transport.download_failed")
            transferred = 0
            while chunk := process.stdout.read(1024 * 1024):
                transferred += len(chunk)
                yield chunk
            if transferred != reply.data.get("size_bytes"):
                raise ConnectionError("transport.download_incomplete")

    @contextmanager
    def open_port(self, endpoint, port):
        if self.proxy:
            raise RuntimeError("transport.login_routes_require_links_process")
        if not 1 <= port <= 65535:
            raise ValueError("invalid_port")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            local_port = listener.getsockname()[1]
        args = self.arguments(endpoint, multiplex=False)
        process = self.popen(
            [
                *args[:-1],
                "-N",
                "-o",
                "ExitOnForwardFailure=yes",
                "-L",
                f"127.0.0.1:{local_port}:127.0.0.1:{port}",
                args[-1],
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ConnectionError("transport.forward_failed")
                try:
                    with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise TimeoutError("transport.forward_timeout")
            yield PortConnection(host="127.0.0.1", port=local_port)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
