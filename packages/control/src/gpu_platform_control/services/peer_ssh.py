"""Account and task policy shared by dispatch, relay admission and the panel."""

from datetime import timedelta
from functools import lru_cache
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat, SSHCertificateBuilder,
    SSHCertificateType, load_pem_private_key, load_ssh_public_key,
)
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from gpu_platform_contracts.messages import Operation
from gpu_platform_contracts.peer_ssh import FEATURE, LEASE_SECONDS, RELAY, PeerIdentity, PeerKeys, PeerLease, alias
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.slots.configuration import cluster_config


def supported(node):
    host = cluster_config().hosts.get(node.name)
    return bool(host and host.features.workspace_ssh
                and node.capabilities.get("features", {}).get(FEATURE))


def cross_node(gateway, node):
    if not supported(node):
        return False
    try:
        module = gateway.module(node, "transport")
    except (LookupError, ValueError):
        return False
    return RELAY in module.descriptor.provides.get("port", set())


def live_workspaces(now):
    return Workspace.objects.filter(
        request__status="running", request__user__is_active=True, request__node__enabled=True,
        cleanup_complete=False, stop_requested=False, ended_at__isnull=True,
    ).filter(Q(request__ends_at__isnull=True) | Q(request__ends_at__gt=now))


def identity(workspace):
    payload = workspace.dispatch_payload or {}
    value = payload.get("extensions", {}).get(FEATURE)
    return PeerIdentity.model_validate(value) if value else None


@lru_cache(maxsize=8)
def authority(directory: Path, label: str):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / label
    if not path.exists():
        material = Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
        with NamedTemporaryFile(dir=directory) as temporary:
            temporary.write(material)
            temporary.flush()
            os.fsync(temporary.fileno())
            try:
                os.link(temporary.name, path)
            except FileExistsError:
                pass
    return load_pem_private_key(path.read_bytes(), None)


def sign(key, public_key, kind, principal, start, end):
    public = load_ssh_public_key(public_key.encode())
    if not isinstance(public, Ed25519PublicKey):
        raise ValueError("peer.invalid_key_type")
    certificate = SSHCertificateBuilder().public_key(public).type(kind).key_id(
        principal.encode()
    ).valid_principals([principal.encode()]).valid_after(start).valid_before(end)
    if kind is SSHCertificateType.USER:
        certificate = certificate.add_extension(b"permit-pty", b"")
    return certificate.sign(key).public_bytes().decode()


def prepare_identity(gateway, node, task_id):
    keys = PeerKeys.model_validate(gateway.request(node, Operation.PEER_KEYS, task_id=str(task_id)))
    directory = settings.DATA_DIR / "workspace-ssh-ca"
    host_key = authority(directory, "host")
    user_key = authority(directory, "user")
    start = int(timezone.now().timestamp())
    return PeerIdentity(
        keys=keys,
        host_certificate=sign(host_key, keys.host, SSHCertificateType.HOST, alias(task_id),
                              start - 60, start + 10 * 365 * 86400),
        host_authority=host_key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode(),
        user_authority=user_key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode(),
    )


@lru_cache(maxsize=4096)
def user_certificate(directory, public_key, owner, interval):
    key = authority(directory, "user")
    return sign(key, public_key, SSHCertificateType.USER, f"account-{owner}", interval - 60, interval + 180)


def refresh_leases(gateway, node):
    now = timezone.now()
    expires_at = now + timedelta(seconds=LEASE_SECONDS)
    interval = int(now.timestamp()) // 60 * 60
    directory = settings.DATA_DIR / "workspace-ssh-ca"
    leases = []
    for ws in live_workspaces(now).filter(request__node=node).select_related("request"):
        peer = identity(ws)
        if peer is None:
            continue
        owner = str(ws.request.user_id)
        leases.append(PeerLease(
            task_id=ws.pk, owner=owner, expires_at=min(ws.request.ends_at or expires_at, expires_at),
            certificate=user_certificate(directory, peer.keys.user, owner, interval),
        ).model_dump(mode="json"))
    gateway.request(node, Operation.PEER_LEASES, leases=leases)


def connection(request):
    ws = getattr(request, "workspace", None)
    if (request.status != "running" or not request.user.is_active or not request.node.enabled
            or request.node.state != "online" or not ws or ws.stop_requested or ws.ended_at
            or ws.cleanup_complete or (request.ends_at and request.ends_at <= timezone.now())
            or not supported(request.node) or identity(ws) is None):
        return None
    name = alias(request.pk)
    return {"alias": name, "command": f"ssh {request.login_user}@{name}"}


def peers(request, gateway):
    if connection(request) is None:
        return []
    result = []
    for ws in live_workspaces(timezone.now()).filter(request__user_id=request.user_id).exclude(
        pk=request.pk
    ).select_related("request__node", "request__user").order_by("request__created_at"):
        target = ws.request
        address = connection(target)
        if address is None:
            continue
        reachable = request.node_id == target.node_id or (
            target.node.state == "online" and request.node.state == "online"
            and cross_node(gateway, request.node) and cross_node(gateway, target.node)
        )
        result.append({"name": target.purpose, "command": address["command"], "reachable": reachable})
    return result


def authorize(node, source: UUID, target: UUID):
    rows = {row.pk: row for row in live_workspaces(timezone.now()).filter(
        pk__in=[source, target], request__node__state="online"
    ).select_related("request__node")}
    a, b = rows.get(source), rows.get(target)
    if not a or not b or a.request.node_id != node.pk or a.request.node_id == b.request.node_id:
        raise PermissionError("peer.invalid_route")
    if a.request.user_id != b.request.user_id or not identity(a) or not identity(b):
        raise PermissionError("peer.forbidden")
    return b.request.node, str(a.request.user_id)
