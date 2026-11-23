"""Workspace SSH handoffs and the shared bounded, half-close-aware relay."""

import logging
import socket
from threading import Thread
from uuid import UUID

from pydantic import AwareDatetime, Field

from .base import Contract

FEATURE = "workspace_ssh"
RELAY = "workspace-ssh"
DOMAIN = "workspaces.internal"
LEASE_SECONDS = 30
SSH_PORT = 2222
logger = logging.getLogger(__name__)


def alias(task_id):
    return f"task-{UUID(str(task_id))}.{DOMAIN}"


class PeerKeys(Contract):
    host: str
    user: str


class PeerIdentity(Contract):
    keys: PeerKeys
    host_certificate: str
    host_authority: str
    user_authority: str


class PeerLease(Contract):
    task_id: UUID
    owner: str
    expires_at: AwareDatetime
    certificate: str


class PeerRequest(Contract):
    session: UUID
    source: UUID
    target: UUID


class PeerTicket(Contract):
    port: int = Field(ge=1, le=65535)
    secret: str = Field(pattern=r"^[a-f0-9]{64}$")


def disconnect(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def relay(left, right):
    def copy(source, target):
        try:
            while data := source.recv(65536):
                target.sendall(data)
            target.shutdown(socket.SHUT_WR)
        except OSError:
            disconnect(source)
            disconnect(target)

    worker = Thread(target=copy, args=(left, right), daemon=True)
    worker.start()
    try:
        copy(right, left)
    finally:
        worker.join()
