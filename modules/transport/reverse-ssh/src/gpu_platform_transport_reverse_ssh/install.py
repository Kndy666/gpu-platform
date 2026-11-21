"""Provision host socket access and controller key permissions."""
from pathlib import Path

from gpu_platform_contracts.module import CheckResult
from gpu_platform_contracts.texts import Message


def check_host(context):
    if "control" not in context.host.get("roles", []):
        return []
    directory = Path(context.options.get("key_directory") or context.state_directory / "ssh-keys")
    identity = Path(context.options.get("identity") or "")
    if not identity.is_absolute():
        identity = directory / identity
    return [CheckResult(ok=identity.is_file() and (directory / "known_hosts").is_file(),
        message=Message(code="transport.controller_keys_required", parameters={"directory": str(directory)}))]


def install_host(context):
    roles = context.host.get("roles", [])
    selected = context.host.get("slots", {}).get("transport") or {}
    if "node" in roles and selected.get("module") == "reverse-ssh":
        user = context.options.get("ssh_user", "gpu-platform")
        if not user or user.startswith("-"):
            raise ValueError("transport.invalid_ssh_user")
        group_id = int(context.host.get("parameters", {}).get("socket_group", 10001))
        result = context.runner(["getent", "group", str(group_id)], capture_output=True, check=False)
        if result.returncode:
            group = "gpu-platform-sockets"
            context.runner(["groupadd", "--gid", str(group_id), group])
        else:
            group = result.stdout.split(":", 1)[0].strip()
        account = context.runner(["id", "-u", user], capture_output=True, check=False)
        if account.returncode:
            context.runner(["useradd", "--system", "--create-home", "--shell", "/bin/sh", user])
        context.runner(["usermod", "--append", "--groups", group, user])
        if group_id != 10001:
            state_group = context.runner(["getent", "group", "10001"], capture_output=True, check=False)
            if state_group.returncode:
                state_name = "gpu-platform-state"
                context.runner(["groupadd", "--gid", "10001", state_name])
            else:
                state_name = state_group.stdout.split(":", 1)[0].strip()
            context.runner(["usermod", "--append", "--groups", state_name, user])
    if "control" in roles:
        directory = Path(context.options.get("key_directory") or context.state_directory / "ssh-keys")
        if not context.options.get("identity"):
            raise FileNotFoundError("transport.controller_keys_required")
        identity = Path(context.options["identity"])
        if not identity.is_absolute():
            identity = directory / identity
        if not identity.is_file() or not (directory / "known_hosts").is_file():
            raise FileNotFoundError("transport.controller_keys_required")
        context.runner(["chown", "10001:10001", str(directory), str(identity), str(directory / "known_hosts")])
        context.runner(["chmod", "0700", str(directory)])
        context.runner(["chmod", "0600", str(identity), str(directory / "known_hosts")])
