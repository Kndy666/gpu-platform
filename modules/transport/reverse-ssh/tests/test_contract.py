import shutil
import socket
import subprocess

import pytest

from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.testing import TransportContract
from gpu_platform_contracts.testing.channels import echo_peer, local_ssh_process, protocol_peer
from gpu_platform_transport_reverse_ssh.module import Module

pytest_plugins = ["gpu_platform_contracts.testing.fixtures"]


@pytest.fixture
def endpoint(tmp_path):
    with protocol_peer(tmp_path / "node.sock") as path:
        yield path


@pytest.fixture
def transport(tmp_path, endpoint):
    return Module(
        ModuleContext(state_directory=tmp_path, options={"socket_path": endpoint, "identity": "fixture-key"}),
        popen=local_ssh_process,
    )


@pytest.fixture
def echo_port():
    with echo_peer() as port:
        yield port


class TestSSHTransportContract(TransportContract):
    pass


@pytest.mark.skipif(shutil.which("ssh") is None, reason="requires the OpenSSH client")
def test_login_listener_does_not_join_the_persistent_ssh_master(transport, endpoint, echo_port, tmp_path):
    config = tmp_path / "ssh_config"
    config.write_text(f"Host *\n  ControlMaster auto\n  ControlPath {tmp_path}/master\n  ControlPersist 60\n")

    def launch(arguments, **options):
        settings = subprocess.check_output(
            ["ssh", "-G", "-F", str(config), *arguments[1:]], text=True
        )
        effective = dict(line.split(" ", 1) for line in settings.splitlines() if " " in line)
        assert effective["controlmaster"] in {"no", "false"}
        assert effective.get("controlpath", "none") == "none"
        return local_ssh_process(arguments, **options)

    transport.popen = launch
    with transport.open_port(endpoint, echo_port) as address:
        with socket.create_connection((address.host, address.port), timeout=2):
            pass
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((address.host, address.port), timeout=2)
