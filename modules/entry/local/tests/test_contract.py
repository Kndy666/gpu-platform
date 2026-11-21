import pytest

from gpu_platform_contracts.handoffs import PortConnection
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.testing import EntryContract
from gpu_platform_entry_local.module import Module

pytest_plugins = ["gpu_platform_contracts.testing.fixtures"]


@pytest.fixture
def entry(tmp_path):
    return Module(ModuleContext(state_directory=tmp_path))


@pytest.fixture
def connection():
    return PortConnection(host="127.0.0.1", port=2222)


@pytest.fixture
def public_port(connection):
    return connection.port


class TestLocalEntryContract(EntryContract):
    pass
