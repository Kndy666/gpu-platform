"""Optional pytest plugin: gpu_platform_contracts.testing.fixtures.

Implementation harnesses supply their slot instances, isolated inputs and probes.
These fixtures only allocate identifiers and poll through public slot interfaces.
"""

import socket
from uuid import uuid4

import pytest

from .helpers import await_result


@pytest.fixture
def task_id():
    return str(uuid4())


@pytest.fixture
def stream_id():
    return str(uuid4())


@pytest.fixture
def owner():
    return "contract-owner-" + uuid4().hex


@pytest.fixture
def username():
    return "contract_member"


@pytest.fixture
def language():
    return "en"


@pytest.fixture
def await_operation():
    return await_result


@pytest.fixture
def await_task():
    def wait(runtime, identifier, states, timeout=60):
        return await_result(
            lambda: runtime.status(identifier), ready=lambda status: status.state in states, timeout=timeout
        )

    return wait


@pytest.fixture
def await_build():
    def wait(builder, identifier, timeout=60):
        return await_result(
            lambda: builder.status(identifier),
            ready=lambda result: result.state in {"completed", "failed"},
            timeout=timeout,
        )

    return wait


@pytest.fixture
def connect_echo():
    def exchange(address, payload):
        with socket.create_connection((address.host, address.port), timeout=5) as connection:
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            with connection.makefile("rb") as response:
                return response.read(len(payload) + 1)

    return exchange
