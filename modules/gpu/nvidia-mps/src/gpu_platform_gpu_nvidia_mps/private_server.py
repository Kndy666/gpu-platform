"""Node-owned MPS servers for workspaces whose CUDA clients run in a separate service."""

import csv
import json
import os
import secrets
import select
import shutil
import signal
import stat
from pathlib import Path

from gpu_platform_contracts.handoffs import GPUWorker

CLIENT = "/run/platform-accelerator"
LIBRARIES = ("libcuda.so.1", "libnccl.so.2", "libnvidia-ml.so.1", "libnvshmem_host.so.3")


class PrivateServer:
    def __init__(self, module, identifier):
        self.module = module
        self.name = "w_" + identifier
        self.directory = module.root / "workers" / identifier
        self.generation = self.directory / "generation" / "ready"
        self.requests = self.directory / "requests"
        self.route = self.directory / "route"

    def exists(self):
        result = self.module.mps("server", "list", "--format=csv,noheader")
        return any(row and row[0].strip() == self.name for row in csv.reader(result.splitlines()))

    def handoff(self, allocation, entries, image):
        worker_env = dict(allocation.environment)
        worker_env["PLATFORM_GPU_LIMITS"] = json.dumps(
            [[entry["uuid"], entry["sm_count"], entry["memory_mib"]] for entry in entries]
        )
        allocation.worker = GPUWorker(
            image=image,
            environment=worker_env,
            client_directory=CLIENT,
            ready_file="/tmp/worker-ready",
            mounts=[
                {"source": str(self.route), "target": "/run/platform-mps"},
                {"source": str(self.generation.parent), "target": "/run/worker-generation"},
                {"source": str(self.requests), "target": "/run/worker-requests", "read_only": False},
            ],
        )
        allocation.environment = {
            "NVIDIA_VISIBLE_DEVICES": "void",
            "LUPINE_SERVER": "127.0.0.1:14833",
            "LD_PRELOAD": ":".join(f"{CLIENT}/{name}" for name in LIBRARIES),
        }
        allocation.mounts = []

    def provision(self, record):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.route.mkdir(mode=0o755, exist_ok=True)
        self.generation.parent.mkdir(mode=0o755, exist_ok=True)
        self.requests.mkdir(mode=0o700, exist_ok=True)
        os.chown(self.requests, 65534, 65534)
        entries = record["allocations"]
        self.module.mps(
            "server",
            "create",
            self.name,
            "--uid=0",
            "--allowed-devices=" + ",".join(entry["uuid"] for entry in entries),
        )
        for entry in entries:
            self.module.mps(
                "sm-partition",
                "create",
                entry["partition"],
                "--server=" + self.name,
                "--device=" + entry["uuid"],
                "--chunks=" + str(entry["sm_count"] // 2),
            )
            self.module.mps(
                "server",
                "set",
                self.name,
                "--device=" + entry["uuid"],
                "--sm-partition=" + entry["partition"],
            )
        source = self.module.pipes / self.name / "default"
        for filename, expected in [("control", stat.S_ISSOCK), ("log", stat.S_ISFIFO)]:
            endpoint = source / filename
            if not expected(endpoint.lstat().st_mode):
                raise RuntimeError("Unexpected MPS namespace endpoint")
            temporary = self.route / (filename + ".new")
            temporary.unlink(missing_ok=True)
            os.link(endpoint, temporary, follow_symlinks=False)
            temporary.replace(self.route / filename)
        # The daemon may defer starting a server until it receives its first client.
        self.publish_generation(secrets.token_hex(16))

    def publish_generation(self, generation):
        temporary = self.generation.with_suffix(".new")
        temporary.write_text(generation)
        temporary.chmod(0o444)
        temporary.replace(self.generation)

    def retire(self):
        # The control daemon can disappear with the node service while pods and leases survive.
        # Recover through the node's ownership and compute-mode checks before touching the route.
        self.module.ensure_server()
        if not self.exists():
            return
        raw = self.module.mps("server", "get", self.name, "pid").strip()
        if raw.isdecimal() and int(raw) > 1:
            pid = int(raw)
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                fd = None
            if fd is not None:
                try:
                    self.stop_owned_process(fd, pid)
                finally:
                    os.close(fd)
        if self.exists():
            self.module.mps("server", "delete", self.name)

    def stop_owned_process(self, fd, pid):
        exited = select.poll()
        exited.register(fd, select.POLLIN)
        if exited.poll(0):
            return
        process = Path("/proc", str(pid))
        try:
            binary = (process / "exe").resolve(strict=True).name
            environment = (process / "environ").read_bytes().split(b"\0")
        except FileNotFoundError:
            if exited.poll(0):
                return
            raise
        prefix = os.fsencode("CUDA_MPS_PIPE_DIRECTORY=" + str(self.module.pipes))
        if (
            binary != "nvidia-cuda-mps-server"
            or not any(item == prefix or item.startswith(prefix + b"/") for item in environment)
            or self.module.mps("server", "get", self.name, "pid").strip() != str(pid)
        ):
            raise RuntimeError("Cannot verify the owner of the private MPS process")
        for sig, timeout in ((signal.SIGTERM, 2400), (signal.SIGKILL, 7000)):
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                return
            if exited.poll(timeout):
                return
        raise TimeoutError("Private MPS server did not stop")

    def reconcile(self, record):
        if not self.generation.is_file():
            return
        generation = self.generation.read_text()
        if generation == "closing":
            self.close()
            return
        if generation == "closed":
            return
        request = self.requests / generation
        try:
            mode = request.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISREG(mode):
            raise RuntimeError("Invalid private GPU recovery request")
        self.retire()
        self.provision(record)
        request.unlink(missing_ok=True)

    def close(self):
        if self.generation.is_file():
            generation = self.generation.read_text()
            if generation == "closed":
                return
            if generation != "closing":
                self.publish_generation("closing")
        self.retire()
        if self.generation.parent.is_dir():
            self.publish_generation("closed")

    def release(self):
        self.close()
        if self.directory.exists():
            shutil.rmtree(self.directory)
