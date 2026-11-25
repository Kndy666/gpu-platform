import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from gpu_platform_gpu_nvidia_mps import private_server


class Driver:
    def __init__(self, pipes):
        self.pipes = pipes
        self.running = False
        self.servers = set()
        self.partitions = {}
        self.fail = None
        self.quotas = []

    def __call__(self, args, **kwargs):
        if args[0] == "nvidia-smi":
            if args[1] == "memory-limits":
                self.quotas.append(args)
            return "Default\n" if "--query-gpu=compute_mode" in args else ""
        if "-d" in args:
            self.running = True
            return ""
        command = args[3:]
        if tuple(command[:2]) == self.fail:
            raise RuntimeError("Injected driver failure")
        if not self.running:
            raise subprocess.CalledProcessError(1, args)
        if command[:2] == ["server", "list"]:
            return "\n".join(f"{name},Ready" for name in sorted(self.servers))
        if command[:2] == ["server", "create"]:
            name = command[2]
            directory = self.pipes / name / "default"
            directory.mkdir(parents=True)
            # Use a short address even when pytest's temporary directory is long.
            fd = os.open(directory, os.O_DIRECTORY)
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
                    control.bind(f"/proc/self/fd/{fd}/control")
            finally:
                os.close(fd)
            os.mkfifo(directory / "log")
            self.servers.add(name)
            self.partitions[name] = set()
        if command[:2] == ["sm-partition", "create"]:
            owner = next(arg.removeprefix("--server=") for arg in command if arg.startswith("--server="))
            self.partitions[owner].add(command[2])
        if command[:2] == ["server", "delete"]:
            name = command[2]
            self.servers.remove(name)
            del self.partitions[name]
            shutil.rmtree(self.pipes / name)
        return ""


@pytest.fixture
def driver(tmp_path, monkeypatch):
    def assign_owner(path, uid, gid):
        assert Path(path).name == "requests" and (uid, gid) == (65534, 65534)

    monkeypatch.setattr(private_server.os, "chown", assign_owner)
    return Driver(tmp_path / "gpu" / "pipes")
