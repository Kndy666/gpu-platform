import pytest

from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.testing import TransportContract
from gpu_platform_contracts.testing.channels import echo_peer, protocol_peer
from gpu_platform_transport_local.module import Module

pytest_plugins = ["gpu_platform_contracts.testing.fixtures"]


@pytest.fixture
def endpoint(tmp_path):
    with protocol_peer(tmp_path / "node.sock") as path:
        yield path


@pytest.fixture
def transport(tmp_path):
    return Module(ModuleContext(state_directory=tmp_path))


@pytest.fixture
def echo_port():
    with echo_peer() as port:
        yield port


class TestLocalTransportContract(TransportContract):
    pass
