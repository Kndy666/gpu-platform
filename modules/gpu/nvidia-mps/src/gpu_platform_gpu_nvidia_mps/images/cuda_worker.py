"""Serve CUDA on the loopback interface of one workspace's pod."""

import ctypes as c
import json
import logging
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import sys
import time
import uuid

LIBRARY = "/opt/lupine/lib/liblupine-server.so"
CLIENT = Path("/run/platform-accelerator")
READY = Path("/tmp/worker-ready")
GENERATION = Path("/run/worker-generation/ready")
REQUESTS = Path("/run/worker-requests")
BUNDLE_FILES = {"libcuda.so.1", "libnccl.so.2", "libnvshmem_host.so.3", "libnvidia-ml.so.1"}


def cuda_check(limits, *, exercise):
    """Check the driver's view inside this worker's device cgroup and MPS namespace."""
    driver = c.CDLL("libcuda.so.1")

    def call(name, *args):
        status = getattr(driver, name)(*args)
        if status:
            raise RuntimeError(f"{name} returned CUDA error {status}")

    call("cuInit", 0)
    count = c.c_int()
    call("cuDeviceGetCount", c.byref(count))
    if count.value != len(limits):
        raise RuntimeError("Unexpected number of worker devices")
    for ordinal, (expected_id, expected_sm, memory_mib) in enumerate(limits):
        device, sm = c.c_int(), c.c_int()
        identity = (c.c_ubyte * 16)()
        call("cuDeviceGet", c.byref(device), ordinal)
        call("cuDeviceGetUuid_v2", c.byref(identity), device)
        call("cuDeviceGetAttribute", c.byref(sm), 16, device)
        if expected_id != "GPU-" + str(uuid.UUID(bytes=bytes(identity))) or sm.value != expected_sm:
            raise RuntimeError(f"CUDA device or SM allocation mismatch at ordinal {ordinal}")
        if not exercise:
            continue
        context = c.c_void_p()
        call("cuDevicePrimaryCtxRetain", c.byref(context), device)
        try:
            call("cuCtxSetCurrent", context)
            free, total = c.c_size_t(), c.c_size_t()
            call("cuMemGetInfo_v2", c.byref(free), c.byref(total))
            if total.value != memory_mib * 1024 * 1024:
                raise RuntimeError(f"CUDA memory allocation mismatch at ordinal {ordinal}")
            address = c.c_uint64()
            call("cuMemAlloc_v2", c.byref(address), c.c_size_t(4))
            try:
                result = c.c_uint32()
                call("cuMemsetD32_v2", address, 42, c.c_size_t(1))
                call("cuMemcpyDtoH_v2", c.byref(result), address, c.c_size_t(4))
                call("cuCtxSynchronize")
                if result.value != 42:
                    raise RuntimeError(f"CUDA readback failed at ordinal {ordinal}")
            finally:
                call("cuMemFree_v2", address)
        finally:
            call("cuCtxSetCurrent", c.c_void_p())
            call("cuDevicePrimaryCtxRelease_v2", device)


def serve(fd):
    engine = c.CDLL(LIBRARY)
    handle = engine.lupine_server_serve_connection_v1
    handle.argtypes = [c.c_int]
    return handle(fd)


def install_client():
    import hashlib
    import http.client
    import io
    import platform
    import shutil
    import zipfile

    # Use the portable ABI client embedded in this exact server build.
    local, child = socket.socketpair()
    with local, child:
        helper = subprocess.Popen(
            [sys.executable, __file__, "bundle", str(child.fileno())], pass_fds=(child.fileno(),)
        )
        child.close()
        try:
            local.settimeout(20)
            arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
            local.sendall(
                f"GET /.well-known/lupine/client/v1/linux/{arch} HTTP/1.1\r\n"
                "Host: localhost\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            with http.client.HTTPResponse(local) as response:
                response.begin()
                if response.status != 200:
                    raise RuntimeError(f"Embedded CUDA client unavailable ({response.status})")
                payload = response.read(64 * 1024 * 1024 + 1)
                if len(payload) > 64 * 1024 * 1024:
                    raise ValueError("Embedded client exceeds expected size")
            if helper.wait(timeout=5):
                raise RuntimeError("Embedded client extraction failed")
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=5)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = {
            entry["path"]: entry["sha256"] for entry in json.loads(archive.read("manifest.json"))["files"]
        }
        for name in sorted(BUNDLE_FILES):
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != files[name]:
                raise ValueError(f"Invalid embedded client digest for {name}")
            temporary = CLIENT / (name + ".new")
            temporary.write_bytes(data)
            temporary.chmod(0o555)
            temporary.replace(CLIENT / name)
        link = CLIENT / "libcuda.so"
        if not link.exists():
            link.symlink_to("libcuda.so.1")
    if executable := shutil.which("nvidia-smi"):
        (CLIENT / "bin").mkdir(exist_ok=True)
        temporary = CLIENT / "bin/nvidia-smi.new"
        shutil.copyfile(executable, temporary)
        temporary.chmod(0o555)
        temporary.replace(CLIENT / "bin/nvidia-smi")


def generation():
    value = GENERATION.read_text()
    if not re.fullmatch("[0-9a-f]{32}", value):
        raise RuntimeError("GPU allocation closed or unavailable")
    return value


def recover(old):
    logging.warning("Recovering private CUDA service")
    (REQUESTS / old).touch(exist_ok=True)
    until = time.monotonic() + 65
    while time.monotonic() < until:
        current = generation()
        if current != old:
            return current
        time.sleep(0.1)
    raise TimeoutError("Private GPU generation did not advance")


def launch(connection):
    parent, gate = socket.socketpair()
    with parent, gate:
        child = subprocess.Popen(
            [sys.executable, __file__, "connection", str(connection.fileno()), str(gate.fileno())],
            pass_fds=(connection.fileno(), gate.fileno()),
        )
        gate.close()
        parent.settimeout(8)
        try:
            if parent.recv(1) == b"A":
                parent.sendall(b"G")
                return child, True
        except (TimeoutError, ConnectionError):
            pass
        return child, False


class Worker:
    def __init__(self, listener):
        self.listener = listener
        self.poll = select.poll()
        self.poll.register(listener, select.POLLIN)
        self.clients = {}
        self.children = set()
        self.last_disconnect = None
        self.dirty = False
        self.epoch = generation()

    def healthy(self):
        probe = subprocess.Popen([sys.executable, __file__, "health"])
        try:
            return probe.wait(timeout=8) == 0
        except subprocess.TimeoutExpired:
            probe.kill()
            self.children.add(probe)
            # The private service is retired before we wait for a potentially blocked driver call.
            return False

    def discard(self, fd):
        connection, _ = self.clients.pop(fd)
        self.poll.unregister(fd)
        connection.close()
        if not self.clients:
            self.last_disconnect = time.monotonic()

    def renew(self):
        self.epoch = recover(self.epoch)
        for child in self.children:
            if child.poll() is None:
                child.kill()
        for child in self.children:
            child.wait(timeout=8)
        self.children.clear()
        if not self.healthy():
            raise RuntimeError("New private GPU service failed functional checks")
        self.dirty = False

    def run(self):
        if Path("/tmp/worker-started").exists() or not self.healthy():
            self.renew()
        Path("/tmp/worker-started").touch()
        install_client()
        READY.touch()
        while True:
            events = dict(self.poll.poll(100))
            if generation() != self.epoch:
                raise RuntimeError("GPU generation changed while serving")
            for fd, (_, child) in list(self.clients.items()):
                if child.poll() is not None or events.get(fd, 0) & (
                    select.POLLRDHUP | select.POLLHUP | select.POLLERR
                ):
                    self.discard(fd)
            for child in tuple(self.children):
                if child.poll() is not None:
                    self.dirty |= child.returncode != 0
                    self.children.remove(child)
            if self.last_disconnect is not None:
                # Retain new connections in the backlog while an exited rank's native work drains.
                if self.children and not self.dirty and time.monotonic() - self.last_disconnect < 0.5:
                    time.sleep(0.025)
                    continue
                if self.children or self.dirty or not self.healthy():
                    self.renew()
                self.last_disconnect = None
            if self.listener.fileno() not in events:
                continue
            connection, _ = self.listener.accept()
            try:
                child, admitted = launch(connection)
                self.children.add(child)
                if not admitted and not self.clients:
                    self.renew()
                    child, admitted = launch(connection)
                    self.children.add(child)
                if admitted:
                    fd = connection.fileno()
                    self.clients[fd] = (connection, child)
                    self.poll.register(fd, select.POLLRDHUP | select.POLLHUP | select.POLLERR)
                else:
                    connection.close()
                    self.dirty = True
            except BaseException:
                connection.close()
                raise


def main():
    if len(sys.argv) == 1:
        READY.unlink(missing_ok=True)
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 14833))
            listener.listen(128)
            try:
                Worker(listener).run()
            finally:
                READY.unlink(missing_ok=True)
        return 0
    if sys.argv[1] == "bundle":
        return serve(int(sys.argv[2]))
    limits = json.loads(os.environ["PLATFORM_GPU_LIMITS"])
    if sys.argv[1] == "health":
        cuda_check(limits, exercise=True)
        return 0
    if sys.argv[1] == "connection":
        cuda_check(limits, exercise=False)
        with socket.socket(fileno=int(sys.argv[3])) as gate:
            gate.sendall(b"A")
            if gate.recv(1) != b"G":
                return 1
        return serve(int(sys.argv[2]))
    raise ValueError("Unknown GPU worker operation")


if __name__ == "__main__":
    sys.exit(main())
