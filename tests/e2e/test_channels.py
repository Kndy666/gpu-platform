"""Command and streaming roundtrips through isolated local endpoints."""
import shlex
import subprocess
import threading
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from gpu_platform_contracts.messages import Command, Operation, Reply
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_control.loop.links_proxy import Server as ProxyServer
from gpu_platform_node.daemon import Server as NodeServer
from gpu_platform_node.sync import Streams
from gpu_platform_transport_local.module import Module as Local
from gpu_platform_transport_reverse_ssh.module import Module as SSH


@contextmanager
def running(server):
    with server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def channel(tmp_path):
    engine = SimpleNamespace(streams=Streams(tmp_path / "streams"),
                             handle=lambda command: Reply(id=command.id, ok=True, data={"value": command.payload}))
    path = tmp_path / "node.sock"
    with running(NodeServer(path, engine)):
        yield path, engine


def bridge(args, **kwargs):
    # Execute the standard-library bridge locally; the test supplies its socket.
    return subprocess.Popen(shlex.split(args[-1]), **kwargs)


@pytest.mark.parametrize("implementation", ["local", "ssh-bridge", "proxy"])
def test_correlated_commands_and_binary_streams(channel, tmp_path, implementation):
    path, engine = channel
    options = {"socket_path": str(path), "identity": "fixture-key"}
    context = ModuleContext(node_id="node-a", state_directory=tmp_path, options=options)
    local = Local(context)
    if implementation == "local":
        module = local
        lifecycle = nullcontext()
    elif implementation == "ssh-bridge":
        module = SSH(context, popen=bridge)
        lifecycle = nullcontext()
    else:
        gateway = SimpleNamespace(module=lambda node, slot: local, endpoint=lambda node: str(path))
        proxy_path = tmp_path / "proxy.sock"
        lifecycle = running(ProxyServer(proxy_path, gateway, resolve_node=lambda name: SimpleNamespace(name=name)))
        context.options["proxy_socket"] = str(proxy_path)
        module = SSH(context)
    with lifecycle:
        command = Command(operation=Operation.STATUS, payload={"sequence": 7})
        reply = module.request(str(path), command)
        assert reply.id == command.id and reply.data["value"] == command.payload
        payload = bytes(range(256)) * 8193
        assert module.send_stream(str(path), "roundtrip", [payload[:700], payload[700:]]) == len(payload)
        assert b"".join(module.receive_stream(str(path), "roundtrip")) == payload
        assert module.online(str(path))


def test_failed_stream_is_reported_without_creating_target(channel, tmp_path):
    path, engine = channel
    module = Local(ModuleContext(state_directory=tmp_path, options={"socket_path": str(path)}))
    with pytest.raises(ConnectionError):
        list(module.receive_stream(str(path), "missing"))
    assert not engine.streams.path("missing").exists()


def test_proxy_detects_upstream_failure_after_first_chunk(tmp_path):
    def broken(endpoint, stream_id):
        yield b"\x00\x01"
        raise ConnectionError()
    transport = SimpleNamespace(receive_stream=broken)
    gateway = SimpleNamespace(module=lambda node, slot: transport, endpoint=lambda node: "fixture")
    path = tmp_path / "proxy.sock"
    with running(ProxyServer(path, gateway, resolve_node=lambda name: SimpleNamespace(name=name))):
        module = SSH(ModuleContext(node_id="node-a", state_directory=tmp_path, options={"proxy_socket": str(path)}))
        chunks = module.receive_stream("fixture", "transfer")
        assert next(chunks) == b"\x00\x01"
        with pytest.raises(ConnectionError):
            next(chunks)
