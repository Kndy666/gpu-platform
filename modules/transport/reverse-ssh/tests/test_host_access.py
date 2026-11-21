from types import SimpleNamespace

from gpu_platform_transport_reverse_ssh.install import install_host


def test_worker_ssh_account_receives_socket_group(tmp_path):
    commands = []
    def runner(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1 if command[0] in {"getent", "id"} else 0, stdout="")
    context = SimpleNamespace(state_directory=tmp_path, options={"ssh_user": "worker-login"},
        host={"roles": ["node"], "slots": {"transport": {"module": "reverse-ssh"}}, "parameters": {"socket_group": 14001}}, runner=runner)
    install_host(context)
    assert ["groupadd", "--gid", "14001", "gpu-platform-sockets"] in commands
    assert ["usermod", "--append", "--groups", "gpu-platform-sockets", "worker-login"] in commands
    assert ["usermod", "--append", "--groups", "gpu-platform-state", "worker-login"] in commands


def test_controller_private_key_is_accessible_only_to_service_identity(tmp_path):
    key = tmp_path / "node-key"
    key.write_bytes(b"fixture-key")
    (tmp_path / "known_hosts").write_bytes(b"fixture-host")
    commands = []
    context = SimpleNamespace(state_directory=tmp_path, options={"key_directory": str(tmp_path), "identity": "node-key"},
        host={"roles": ["control"], "slots": {}}, runner=lambda command, **kwargs: commands.append(command))
    install_host(context)
    assert ["chmod", "0600", str(key), str(tmp_path / "known_hosts")] in commands
    assert any(command[:2] == ["chown", "10001:10001"] for command in commands)
