"""Task-local SSH brokers and controller-admitted relay legs."""

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import subprocess
from threading import BoundedSemaphore, Event, RLock, Thread
from time import monotonic, sleep
from uuid import UUID, uuid4

from gpu_platform_contracts.peer_ssh import LEASE_SECONDS, PeerKeys, PeerLease, PeerRequest, PeerTicket, disconnect, relay

logger = logging.getLogger(__name__)
HANDSHAKE = 30
SWEEP_SECONDS = 1


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


def server(socket_type, address, accept):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                accept(self.request)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                return

    return socket_type(address, Handler)


def line(sock, limit):
    data = bytearray()
    while len(data) <= limit:
        char = sock.recv(1)
        if char == b"\n":
            return data.decode("ascii")
        if not char:
            break
        data.extend(char)
    raise ValueError("peer.invalid_frame")


@dataclass
class Workspace:
    directory: Path
    servers: list
    lease: PeerLease | None = None
    agent: subprocess.Popen | None = None


@dataclass
class Pending:
    request: PeerRequest
    stream: socket.socket
    deadline: float
    claimed: bool = False


@dataclass
class Active:
    tasks: tuple[str, ...]
    sockets: tuple[socket.socket, socket.socket]


class PeerBroker:
    def __init__(self, root, endpoint, audit=lambda *_: None):
        self.root, self.endpoint, self.audit = root, endpoint, audit
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = RLock()
        self.capacity = BoundedSemaphore(64)
        self.stopping = Event()
        self.tasks: dict[str, Workspace] = {}
        self.pending: dict[UUID, Pending] = {}
        self.tickets: dict[str, tuple[str, UUID | None, float]] = {}
        self.active: dict[UUID, Active] = {}
        self.relay = server(TCPServer, ("127.0.0.1", 0), self.accept_relay)
        self.start_server(self.relay)
        self.sweeper = Thread(target=self.sweep, daemon=True)
        self.sweeper.start()

    @staticmethod
    def start_server(listener):
        Thread(target=listener.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    def keys(self, identifier):
        task_id = str(UUID(identifier))
        directory = self.root / task_id
        with self.lock:
            directory.mkdir(mode=0o700, exist_ok=True)
            for name in ("host", "user"):
                key = directory / name
                if not key.exists():
                    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                                   check=True, capture_output=True, timeout=5)
            return PeerKeys(host=(directory / "host.pub").read_text().strip(),
                            user=(directory / "user.pub").read_text().strip())

    def prepare(self, task_id, identity):
        with self.lock:
            if self.keys(task_id) != identity.keys:
                raise PermissionError("peer.key_mismatch")
            if task_id in self.tasks:
                return
            root = self.root / task_id
            directory = root / "mount"
            directory.mkdir(mode=0o755, exist_ok=True)
            host_key = directory / "host"
            shutil.copyfile(root / "host", host_key)
            host_key.chmod(0o444)
            servers = []
            try:
                for name, accept in (("connect", self.accept_task), ("identity", self.accept_agent)):
                    path = directory / name
                    path.unlink(missing_ok=True)
                    listener = server(UnixServer, str(path), lambda sock, accept=accept: accept(task_id, sock))
                    self.start_server(listener)
                    servers.append(listener)
                    path.chmod(0o666)
            except BaseException:
                for listener in servers:
                    listener.shutdown()
                    listener.server_close()
                raise
            self.tasks[task_id] = Workspace(root, servers)

    def valid(self, task_id):
        task = self.tasks.get(task_id)
        if not task or not task.lease or task.lease.expires_at <= datetime.now(timezone.utc):
            raise PermissionError("peer.expired")
        return task

    def lease(self, grants, owners):
        now = datetime.now(timezone.utc)
        with self.lock:
            for task_id, task in self.tasks.items():
                grant = grants.get(task_id)
                previous = task.lease
                if not grant or grant.owner != owners.get(task_id) or not (
                    now < grant.expires_at <= now + timedelta(seconds=LEASE_SECONDS + 5)
                ):
                    self.revoke(task_id)
                    continue
                if (not previous or previous.certificate != grant.certificate
                        or not task.agent or task.agent.poll() is not None):
                    try:
                        self.load_agent(task, grant.certificate)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        self.revoke(task_id)
                        logger.warning("Workspace SSH agent update failed for %s", task_id)
                        continue
                deadline = now + timedelta(seconds=LEASE_SECONDS - SWEEP_SECONDS)
                task.lease = grant.model_copy(update={"expires_at": min(grant.expires_at, deadline)})

    def load_agent(self, task, certificate):
        root = task.directory
        path = root / "agent"
        if not task.agent or task.agent.poll() is not None:
            path.unlink(missing_ok=True)
            task.agent = subprocess.Popen(["ssh-agent", "-D", "-a", str(path)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                if path.exists() or task.agent.poll() is not None:
                    break
                sleep(0.02)
        (root / "user-cert.pub").write_text(certificate + "\n")
        env = {**os.environ, "SSH_AUTH_SOCK": str(path)}
        subprocess.run(["ssh-add", "-D"], env=env, check=True, capture_output=True, timeout=3)
        subprocess.run(["ssh-add", str(root / "user")], env=env, check=True, capture_output=True, timeout=3)
        result = subprocess.run(["ssh-add", "-L"], env=env, check=True, capture_output=True, timeout=3)
        if not any(line.startswith(b"ssh-ed25519-cert-v01@openssh.com ") for line in result.stdout.splitlines()):
            raise ValueError("peer.certificate_not_loaded")

    def revoke(self, task_id):
        with self.lock:
            if task_id in self.tasks:
                self.tasks[task_id].lease = None
            for stream in self.pending.values():
                if str(stream.request.source) == task_id:
                    disconnect(stream.stream)
            for session in self.active.values():
                if task_id in session.tasks:
                    for sock in session.sockets:
                        disconnect(sock)

    def forget(self, task_id, *, remove=True):
        self.revoke(task_id)
        with self.lock:
            task = self.tasks.pop(task_id, None)
        if task:
            for listener in task.servers:
                listener.shutdown()
                listener.server_close()
            if task.agent and task.agent.poll() is None:
                task.agent.terminate()
                task.agent.wait(timeout=5)
        if remove:
            directory = self.root / str(UUID(task_id))
            if directory.exists():
                shutil.rmtree(directory)

    def run_session(self, tasks, left, right):
        session = uuid4()
        with self.lock:
            for task_id in tasks:
                self.valid(task_id)
            self.active[session] = Active(tasks, (left, right))
        try:
            left.settimeout(None)
            right.settimeout(None)
            relay(left, right)
        finally:
            with self.lock:
                del self.active[session]

    def accept_agent(self, source, client):
        if not self.capacity.acquire(blocking=False):
            return
        try:
            with self.lock:
                path = self.valid(source).directory / "agent"
            with socket.socket(socket.AF_UNIX) as agent:
                agent.settimeout(3)
                agent.connect(str(path))
                self.run_session((source,), client, agent)
        finally:
            self.capacity.release()

    def accept_task(self, source, client):
        if not self.capacity.acquire(blocking=False):
            return
        try:
            client.settimeout(3)
            target = str(UUID(line(client, 36)))
            with self.lock:
                lease = self.valid(source).lease
                assert lease is not None
                owner = lease.owner
                local = target in self.tasks
                if local:
                    target_lease = self.valid(target).lease
                    if not target_lease or target_lease.owner != owner:
                        self.audit(source, target, False)
                        raise PermissionError("peer.forbidden")
            if local:
                try:
                    destination = self.connect_target(target, owner)
                except (OSError, ValueError, KeyError):
                    self.audit(source, target, False)
                    raise
                with destination:
                    self.audit(source, target, True)
                    self.run_session((source, target), client, destination)
                return
            with ExitStack() as stack:
                outgoing, incoming = socket.socketpair()
                stack.enter_context(outgoing)
                stack.enter_context(incoming)
                request = PeerRequest(session=uuid4(), source=UUID(source), target=UUID(target))
                with self.lock:
                    self.valid(source)
                    self.pending[request.session] = Pending(request, incoming, monotonic() + HANDSHAKE)
                try:
                    self.run_session((source,), client, outgoing)
                finally:
                    with self.lock:
                        self.pending.pop(request.session, None)
        finally:
            self.capacity.release()

    def connect_target(self, task_id, owner):
        with self.lock:
            lease = self.valid(task_id).lease
            if not lease or lease.owner != owner:
                raise PermissionError("peer.forbidden")
        endpoint = self.endpoint(task_id, owner)
        if endpoint is None:
            raise PermissionError("peer.unavailable")
        return socket.create_connection((endpoint.host, endpoint.port), timeout=5)

    def requests(self):
        with self.lock:
            return [item.request.model_dump(mode="json") for item in self.pending.values()
                    if not item.claimed and item.deadline > monotonic()]

    def ticket(self, task_id, session=None):
        secret = secrets.token_hex(32)
        self.tickets[secret] = (task_id, session, monotonic() + HANDSHAKE)
        return PeerTicket(port=self.relay.server_address[1], secret=secret)

    def claim(self, request):
        with self.lock:
            entry = self.pending[request.session]
            if entry.claimed or entry.request != request or entry.deadline <= monotonic():
                raise ValueError("peer.invalid_session")
            self.valid(str(request.source))
            entry.claimed = True
            entry.deadline = monotonic() + HANDSHAKE
            return self.ticket(str(request.source), request.session)

    def approve(self, target, owner):
        with self.lock:
            lease = self.valid(target).lease
            if not lease or lease.owner != owner or len(self.tickets) >= 64:
                raise PermissionError("peer.forbidden")
            return self.ticket(target)

    def reject(self, session):
        with self.lock:
            if session in self.pending:
                disconnect(self.pending[session].stream)

    def accept_relay(self, client):
        if not self.capacity.acquire(blocking=False):
            return
        try:
            client.settimeout(3)
            secret = line(client, 64)
            with self.lock:
                task_id, session, deadline = self.tickets.pop(secret)
                if deadline <= monotonic():
                    raise PermissionError("peer.expired_ticket")
                lease = self.valid(task_id).lease
                assert lease is not None
                if session is not None:
                    pending = self.pending[session]
                    pending.deadline = float("inf")
            if session is None:
                with self.connect_target(task_id, lease.owner) as destination:
                    client.sendall(b"OK\n")
                    self.run_session((task_id,), client, destination)
            else:
                client.sendall(b"OK\n")
                self.run_session((task_id,), client, pending.stream)
        finally:
            self.capacity.release()

    def sweep(self):
        while not self.stopping.wait(SWEEP_SECONDS):
            with self.lock:
                now = datetime.now(timezone.utc)
                for task_id, task in self.tasks.items():
                    if task.lease and task.lease.expires_at <= now:
                        self.revoke(task_id)
                for entry in self.pending.values():
                    if entry.deadline <= monotonic():
                        disconnect(entry.stream)
                self.tickets = {key: value for key, value in self.tickets.items() if value[2] > monotonic()}

    def close(self):
        self.stopping.set()
        self.sweeper.join()
        self.relay.shutdown()
        self.relay.server_close()
        for task_id in list(self.tasks):
            self.forget(task_id, remove=False)
