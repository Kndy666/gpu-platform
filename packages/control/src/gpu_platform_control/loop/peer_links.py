"""Authorize node-initiated SSH streams and join the two private transport legs."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import logging
import socket
from threading import Lock
from time import monotonic

from pydantic import TypeAdapter

from gpu_platform_contracts.messages import Operation
from gpu_platform_contracts.peer_ssh import PeerRequest, PeerTicket, disconnect, relay
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.services import audit
from gpu_platform_control.services.peer_ssh import authorize, cross_node, refresh_leases, supported

logger = logging.getLogger(__name__)
commands = TypeAdapter(list[PeerRequest])


def attach(stack, gateway, node, ticket):
    transport = gateway.module(node, "transport")
    address = stack.enter_context(transport.open_port(gateway.endpoint(node), ticket.port))
    connection = stack.enter_context(socket.create_connection((address.host, address.port), timeout=5))
    connection.sendall(ticket.secret.encode("ascii") + b"\n")
    with connection.makefile("rb", buffering=0) as stream:
        if stream.readline(4) != b"OK\n":
            raise ConnectionError("peer.relay_rejected")
    connection.settimeout(None)
    return connection


class PeerLinks:
    def __init__(self, gateway):
        self.gateway = gateway
        self.pollers = ThreadPoolExecutor(max_workers=8, thread_name_prefix="peer-poll")
        self.workers = ThreadPoolExecutor(max_workers=32, thread_name_prefix="peer-relay")
        self.polling = {}
        self.next_refresh = {}
        self.sessions = {}
        self.sockets = {}
        self.lock = Lock()
        self.closed = False

    def tick(self):
        for node_id, future in list(self.polling.items()):
            if future.done():
                del self.polling[node_id]
                try:
                    future.result()
                except Exception:
                    logger.warning("Workspace SSH relay polling failed for node %s", node_id)

        now = monotonic()
        nodes = {node.pk: node for node in ComputeNode.objects.filter(enabled=True, state="online")
                 if supported(node)}
        self.next_refresh = {key: time for key, time in self.next_refresh.items() if key in nodes}
        for node_id, node in nodes.items():
            if node_id in self.polling:
                continue
            renew = now >= self.next_refresh.get(node_id, 0)
            forward = cross_node(self.gateway, node)
            if renew:
                self.next_refresh[node_id] = now + 10
            if renew or forward:
                self.polling[node_id] = self.pollers.submit(self.pending, node, renew, forward)

        with self.lock:
            sessions = list(self.sessions.items())
        for session, future in sessions:
            if future.done():
                with self.lock:
                    self.sessions.pop(session, None)
                try:
                    future.result()
                except Exception:
                    logger.exception("Workspace SSH relay session failed")

    def pending(self, node, renew, forward):
        from django.db import close_old_connections

        close_old_connections()
        try:
            if renew:
                try:
                    refresh_leases(self.gateway, node)
                except Exception:
                    logger.exception("Workspace SSH lease refresh failed for %s", node.name)
            if not forward:
                return
            result = self.gateway.request(node, Operation.PEER_PENDING)
            for request in commands.validate_python(result["requests"]):
                with self.lock:
                    if self.closed or len(self.sessions) >= 32 or request.session in self.sessions:
                        continue
                    self.sessions[request.session] = self.workers.submit(self.forward, node, request)
        finally:
            close_old_connections()

    def forward(self, node, request):
        from django.db import close_old_connections

        opened = False
        close_old_connections()
        try:
            target, owner = authorize(node, request.source, request.target)
            if not cross_node(self.gateway, node) or not cross_node(self.gateway, target):
                raise PermissionError("peer.transport_unavailable")
            destination = PeerTicket.model_validate(self.gateway.request(
                target, Operation.PEER_TARGET, target=str(request.target), owner=owner,
            ))
            origin = PeerTicket.model_validate(self.gateway.request(
                node, Operation.PEER_SOURCE, **request.model_dump(mode="json"),
            ))
            with ExitStack() as stack:
                remote = attach(stack, self.gateway, target, destination)
                local = attach(stack, self.gateway, node, origin)
                with self.lock:
                    if self.closed:
                        return
                    self.sockets[request.session] = (local, remote)
                authorize(node, request.source, request.target)
                audit(None, "workspace_ssh_connected", request.target, source=str(request.source), node=target.name)
                opened = True
                close_old_connections()
                relay(local, remote)
        except Exception:
            if not opened:
                try:
                    if supported(node):
                        self.gateway.request(node, Operation.PEER_REJECT, session=str(request.session))
                except Exception:
                    logger.debug("Workspace SSH source already unavailable", exc_info=True)
                audit(None, "workspace_ssh_rejected", request.target, source=str(request.source), node=node.name)
        finally:
            with self.lock:
                self.sockets.pop(request.session, None)
            close_old_connections()

    def close(self):
        with self.lock:
            self.closed = True
            for pair in self.sockets.values():
                for stream in pair:
                    disconnect(stream)
        self.pollers.shutdown(wait=False, cancel_futures=True)
        self.workers.shutdown(wait=False, cancel_futures=True)
