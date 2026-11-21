import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from gpu_platform_contracts.messages import Operation
from gpu_platform_contracts.texts import Message
from gpu_platform_control.core.audit.models import AuditLog
from gpu_platform_control.core.builds import BuildReconciler
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact, ImageBuild
from gpu_platform_control.services.commands import submit_build
from gpu_platform_control.slots.gateway import NodeCommandError

pytestmark = pytest.mark.django_db


@pytest.fixture
def builds():
    user = get_user_model().objects.create_user("builder")
    node = ComputeNode.objects.create(name="main", architecture="amd64", state="online",
        capabilities={"modules": [{"slot": "build"}, {"slot": "registry"}]})
    first, second = [ImageBuild.objects.create(user=user, node=node, name=name, dockerfile="FROM scratch")
                     for name in ("first", "second")]
    return user, node, first, second


def test_permanent_rejection_finishes_build_and_continues_other_builds(builds):
    _, node, first, second = builds
    calls = []

    def request(node, operation, **payload):
        assert operation == Operation.IMAGE_BUILD
        calls.append(payload["id"])
        if payload["id"] == str(first.pk):
            raise NodeCommandError(Message(code="command.invalid"))
        return {"id": payload["id"], "state": "running"}

    coordinator = BuildReconciler(SimpleNamespace(request=request), node)
    coordinator.tick()
    coordinator.tick()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == "failed"
    assert json.loads(first.error)["code"] == "command.invalid"
    assert second.status == "building"
    assert calls.count(str(first.pk)) == 1
    assert AuditLog.objects.filter(action="image_build_failed", target=str(first.pk)).count() == 1


@pytest.mark.parametrize("code", ["operation.pending", "command.failed", "node.maintenance"])
def test_retryable_response_keeps_build_active_and_allows_other_work(builds, code):
    _, node, first, second = builds

    def request(node, operation, **payload):
        if payload["id"] == str(first.pk):
            raise NodeCommandError(Message(code=code))
        return {"id": payload["id"], "state": "running"}

    BuildReconciler(SimpleNamespace(request=request), node).tick()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status in {"submitted", "building"}
    assert second.status == "building"
    assert not AuditLog.objects.filter(action="image_build_failed").exists()


def test_dispatch_input_survives_catalog_changes_connection_loss_and_restart(builds):
    user, node, first, second = builds
    second.delete()
    image = ImageArtifact.objects.create(node=node, owner=user, name="base", reference="registry/base",
        verified=True, shared=True, architecture="amd64")
    calls = []

    def request(node, operation, **payload):
        assert ImageBuild.objects.get(pk=first.pk).dispatch_payload == payload
        calls.append(deepcopy(payload))
        if len(calls) == 1:
            raise ConnectionError()
        return {"id": payload["id"], "state": "running"}

    gateway = SimpleNamespace(request=request)
    BuildReconciler(gateway, node).tick()
    image.delete()
    ImageArtifact.objects.create(node=node, owner=user, name="different", reference="registry/different",
        verified=True, shared=True, architecture="amd64")
    ImageBuild.objects.filter(pk=first.pk).update(dockerfile="FROM different")
    BuildReconciler(gateway, node).tick()
    assert calls[0] == calls[1]
    first.refresh_from_db()
    assert first.status == "building"


@pytest.mark.parametrize("context", [
    "https://gitee.com/team/project.git", "https://git.campus.example:8443/team/project.git",
])
def test_submit_build_accepts_https_repository_on_any_host(builds, context):
    user, node, first, second = builds
    first.delete()
    second.delete()
    record = submit_build(user, {"node": node.pk, "name": "custom", "dockerfile": "FROM scratch",
                                "context_git": context})
    assert ImageBuild.objects.get(pk=record["id"]).context_git == context
