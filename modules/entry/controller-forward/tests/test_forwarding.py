import socket
import socketserver
import threading

import pytest

from gpu_platform_contracts.handoffs import PortConnection
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.testing import EntryContract
from gpu_platform_entry_controller_forward.module import Module

pytest_plugins = ["gpu_platform_contracts.testing.fixtures"]


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(65536):
            self.request.sendall(data)


def test_forward_lifecycle_and_half_close(tmp_path):
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo) as upstream:
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            public_port = reserved.getsockname()[1]
        entry = Module(
            ModuleContext(
                state_directory=tmp_path, options={"bind_host": "127.0.0.1", "public_host": "127.0.0.1"}
            )
        )
        target = PortConnection(host="127.0.0.1", port=upstream.server_address[1])
        try:
            address = entry.open("task", "member", target, public_port)
            assert entry.open("task", "member", target, public_port) == address
            with socket.create_connection((address.host, address.port), timeout=2) as client:
                payload = bytes(range(256)) * 1024
                client.sendall(payload)
                client.shutdown(socket.SHUT_WR)
                response = bytearray()
                while chunk := client.recv(65536):
                    response.extend(chunk)
                assert response == payload
            renamed = entry.open("task", "newmember", target, public_port)
            assert renamed.username == "newmember"
            entry.close("task")
            entry.close("task")
            assert entry.address("task") is None
            with pytest.raises(OSError):
                socket.create_connection((address.host, address.port), timeout=0.2)
        finally:
            entry.close("task")
            upstream.shutdown()
            thread.join(timeout=5)


def test_invalid_port_has_no_listener(tmp_path):
    entry = Module(ModuleContext(state_directory=tmp_path))
    for port in (None, 0, 65536):
        with pytest.raises(ValueError):
            entry.open("task", "member", PortConnection(host="127.0.0.1", port=22), port)
    assert not entry.listeners


@pytest.fixture
def entry(tmp_path):
    return Module(ModuleContext(state_directory=tmp_path, options={"bind_host": "127.0.0.1"}))


@pytest.fixture
def connection():
    return PortConnection(host="127.0.0.1", port=2222)


@pytest.fixture
def public_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class TestForwardEntryContract(EntryContract):
    pass
