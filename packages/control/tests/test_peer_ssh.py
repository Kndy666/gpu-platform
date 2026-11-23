from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_ssh_public_identity
from django.contrib.auth import get_user_model
from django.utils import timezone

from gpu_platform_contracts.messages import Operation
from gpu_platform_contracts.peer_ssh import FEATURE, PeerLease
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.requests.models import ResourceRequest
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.tasks.models import Workspace
from gpu_platform_control.services.peer_ssh import authorize, cross_node, refresh_leases

pytestmark = pytest.mark.django_db


def task(node, owner):
    artifact = ImageArtifact.objects.create(node=node, owner=owner, name="image", reference="image", verified=True)
    request = ResourceRequest.objects.create(
        node=node, user=owner, image=artifact, purpose="workspace", mode="batch", status="running",
    )
    return Workspace.objects.create(request=request, dispatch_payload={"extensions": {"workspace_ssh": {
        "keys": {"host": "test-host", "user": "test-user"},
        "host_certificate": "test-certificate", "host_authority": "test-authority", "user_authority": "test-authority",
    }}})


def test_peer_authorization_follows_live_ownership_and_revocation():
    owner, other = (get_user_model().objects.create_user(name) for name in ("peer", "other"))
    a, b = (ComputeNode.objects.create(name=name, state="online") for name in ("peer-a", "peer-b"))
    source, target, foreign = task(a, owner), task(b, owner), task(b, other)
    assert authorize(a, source.pk, target.pk) == (b, str(owner.pk))
    with pytest.raises(PermissionError):
        authorize(b, source.pk, target.pk)
    with pytest.raises(PermissionError):
        authorize(a, source.pk, foreign.pk)

    target.stop_requested = True
    target.save(update_fields=["stop_requested"])
    with pytest.raises(PermissionError):
        authorize(a, source.pk, target.pk)
    target.stop_requested = False
    target.save(update_fields=["stop_requested"])
    ResourceRequest.objects.filter(pk=source.pk).update(status="succeeded")
    with pytest.raises(PermissionError):
        authorize(a, source.pk, target.pk)


def test_lease_limits_task_access_and_uses_an_account_certificate(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    owner = get_user_model().objects.create_user("peer")
    node = ComputeNode.objects.create(name="main", state="online")
    workspace = task(node, owner)
    end = timezone.now() + timedelta(seconds=20)
    ResourceRequest.objects.filter(pk=workspace.pk).update(
        mode="interactive", starts_at=end - timedelta(minutes=1), ends_at=end,
    )
    public_key = Ed25519PrivateKey.generate().public_key()
    workspace.dispatch_payload["extensions"][FEATURE]["keys"]["user"] = public_key.public_bytes(
        Encoding.OpenSSH, PublicFormat.OpenSSH
    ).decode()
    workspace.save(update_fields=["dispatch_payload"])
    calls = []
    gateway = SimpleNamespace(request=lambda target, operation, **payload: calls.append((target, operation, payload)))

    refresh_leases(gateway, node)
    target, operation, payload = calls.pop()
    assert (target, operation) == (node, Operation.PEER_LEASES)
    lease, = map(PeerLease.model_validate, payload["leases"])
    assert (lease.task_id, lease.owner, lease.expires_at) == (workspace.pk, str(owner.pk), end)
    certificate = load_ssh_public_identity(lease.certificate.encode())
    assert certificate.valid_principals == [f"account-{owner.pk}".encode()]
    assert certificate.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH) == public_key.public_bytes(
        Encoding.OpenSSH, PublicFormat.OpenSSH
    )

    owner.is_active = False
    owner.save(update_fields=["is_active"])
    refresh_leases(gateway, node)
    assert calls == [(node, Operation.PEER_LEASES, {"leases": []})]


def test_cross_node_requires_advertised_transport_capability(settings):
    settings.PLATFORM_CLUSTER_CONFIG = {"hosts": {"main": {"roles": ["control", "node"]}}}
    node = ComputeNode(name="main", capabilities={"features": {"workspace_ssh": True}},
                       last_seen=timezone.now() - timedelta(seconds=1))
    module = SimpleNamespace(descriptor=SimpleNamespace(provides={"port": {"tcp"}}))
    gateway = SimpleNamespace(module=lambda *_: module)
    assert not cross_node(gateway, node)
    module.descriptor.provides["port"].add("workspace-ssh")
    assert cross_node(gateway, node)
