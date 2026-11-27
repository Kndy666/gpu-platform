import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from gpu_platform_control.core.nodes.models import ComputeNode
from gpu_platform_control.core.retention.models import ImageArtifact
from gpu_platform_control.core.sync.models import SyncJob
from gpu_platform_control.services.synchronization import request_image_sync, request_dataset_sync, run_sync

pytestmark = pytest.mark.django_db
GIB = 1024**3


@pytest.fixture
def transfer_data():
    user = get_user_model().objects.create_user("owner")
    limits = {**user.profile.resource_limits, "resources": {"image_storage": "10"}}
    user.profile.resource_limits = limits
    user.profile.save(update_fields=["resource_limits"])
    capabilities = {"features": {"image_sync": True, "dataset_sync": True}}
    first = ComputeNode.objects.create(name="source", state="online", capabilities=capabilities)
    second = ComputeNode.objects.create(name="target", state="online", capabilities=capabilities)
    from gpu_platform_contracts.messages import CapabilityReport
    from gpu_platform_contracts.module import ModuleDescriptor
    from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage
    from gpu_platform_contracts.texts import LocalizedText
    from gpu_platform_control.services.resources import register_capabilities

    for node in [first, second]:
        register_capabilities(
            node.pk,
            CapabilityReport(
                node_id=node.name,
                architecture="amd64",
                features=capabilities["features"],
                modules=[
                    ModuleDescriptor(
                        name="registry",
                        slot="registry",
                        side="node",
                        version="1",
                        resources=[
                            ResourceDefinition(
                                key="image_storage",
                                unit="GiB",
                                accounting="image",
                                requestable=False,
                                label=LocalizedText(en="Images"),
                            )
                        ],
                    )
                ],
                resources=ResourceUsage(total={"image_storage": 100}),
            ),
        )
    from gpu_platform_control.core.quotas.models import ResourceQuota

    ResourceQuota.objects.update_or_create(
        group=user.profile.group, resource_id="image_storage", defaults={"limit": 10}
    )
    image = ImageArtifact.objects.create(
        node=first,
        owner=user,
        name="sample",
        reference="owner/sample",
        architecture="amd64",
        verified=True,
        size_bytes=4 * GIB,
    )
    return user, first, second, image


def test_copy_accounting_includes_pending_jobs_and_is_idempotent(transfer_data):
    user, first, second, image = transfer_data
    job = request_image_sync(user, image, second)
    assert request_image_sync(user, image, second).pk == job.pk
    third = ComputeNode.objects.create(name="other", state="online", capabilities=second.capabilities)
    from gpu_platform_control.core.quotas.models import NodeResource

    NodeResource.objects.create(node=third, resource_id="image_storage", total=100)
    with pytest.raises(ValidationError) as error:
        request_image_sync(user, image, third)
    assert error.value.code == "resource_quota_exceeded"
    assert SyncJob.objects.count() == 1


def test_transfer_creates_independent_owned_copy(transfer_data):
    user, first, second, image = transfer_data
    job = request_image_sync(user, image, second)

    def transfer(job, progress):
        progress(GIB, 4 * GIB)
        assert SyncJob.objects.get(pk=job.pk).transferred_bytes == GIB
        return {"reference": "owner/copy", "digest": "sha256:123", "size_bytes": 4 * GIB}

    finished = run_sync(job.pk, transfer)
    assert finished.state == "completed"
    assert finished.result_image.node_id == second.pk
    assert finished.result_image.owner_id == user.pk
    assert finished.result_image.pk != image.pk
    assert not finished.result_image.shared
    assert finished.result_image.recycle_after is None
    assert finished.transferred_bytes == 4 * GIB
    assert run_sync(job.pk, lambda *args: pytest.fail("duplicate transfer")).state == "completed"


def test_failed_transfer_releases_reservation_and_records_structured_error(transfer_data):
    user, first, second, image = transfer_data
    job = request_image_sync(user, image, second)

    def interrupted(*args):
        raise OSError()

    failed = run_sync(job.pk, interrupted)
    assert failed.state == "failed"
    assert failed.error["code"] == "sync_failed"
    assert failed.result_image_id is None
    retry = request_image_sync(user, image, second)
    assert retry.pk != failed.pk


def test_image_access_and_architecture_gate(transfer_data):
    user, first, second, image = transfer_data
    stranger = get_user_model().objects.create_user("stranger")
    with pytest.raises(PermissionDenied):
        request_image_sync(stranger, image, second)
    image.shared = True
    image.save(update_fields=["shared"])
    with pytest.raises(PermissionDenied):
        request_image_sync(stranger, image, second)
    with pytest.raises(PermissionDenied):
        request_image_sync(user, image, second)
    image.shared = False
    image.save(update_fields=["shared"])
    second.architecture = "arm64"
    second.save(update_fields=["architecture"])
    with pytest.raises(ValidationError) as error:
        request_image_sync(user, image, second)
    assert error.value.code == "image_architecture_mismatch"


def test_synchronization_catalog_and_jobs_follow_account_permissions(transfer_data):
    from django.test import Client
    from gpu_platform_control.core.api_auth import issue_token

    member, source, target, image = transfer_data
    other = get_user_model().objects.create_user("other-member")
    private = ImageArtifact.objects.create(node=source, owner=other, name="private", reference="other/private",
                                          verified=True, architecture="amd64")
    shared = ImageArtifact.objects.create(node=source, owner=other, name="shared", reference="other/shared",
                                         verified=True, shared=True, architecture="amd64")
    temporary = ImageArtifact.objects.create(
        node=source, owner=member, name="temporary", reference="member/temporary",
        verified=True, temporary=True, architecture="amd64",
    )
    other_job = request_image_sync(other, private, target)
    client = Client(HTTP_AUTHORIZATION="Bearer " + issue_token(member)["token"])
    response = client.post(f"/api/v1/images/{image.pk}/sync", data={"target": target.pk},
                           content_type="application/json")
    assert response.status_code == 202
    own_job = response.json()["data"]["id"]
    jobs = client.get("/api/v1/syncs").json()["data"]["items"]
    assert {row["id"] for row in jobs} == {own_job}
    assert str(other_job.pk) not in {row["id"] for row in jobs}
    assert client.post(f"/api/v1/images/{private.pk}/sync", data={"target": target.pk},
                       content_type="application/json").status_code == 403
    assert client.post(f"/api/v1/images/{shared.pk}/sync", data={"target": target.pk},
                       content_type="application/json").status_code == 403
    assert client.post(f"/api/v1/images/{temporary.pk}/sync", data={"target": target.pk},
                       content_type="application/json").status_code == 400
    assert not SyncJob.objects.filter(image=temporary).exists()
    options = client.get("/api/v1/syncs/options").json()["data"]
    assert {row["id"] for row in options["images"]} == {str(image.pk)}
    assert not options["can_sync_datasets"]
    member.is_staff = True
    member.save(update_fields=["is_staff"])
    options = client.get("/api/v1/syncs/options").json()["data"]
    assert options["can_sync_datasets"]
    assert {row["id"] for row in options["images"]} == {str(image.pk), str(private.pk), str(shared.pk)}
    jobs = client.get("/api/v1/syncs").json()["data"]["items"]
    assert {row["id"] for row in jobs} == {own_job, str(other_job.pk)}


def test_administrator_shared_image_sync_keeps_copy_shared(transfer_data):
    member, _, target, image = transfer_data
    member.is_staff = True
    image.shared = True
    image.save(update_fields=["shared"])
    job = request_image_sync(member, image, target)
    finished = run_sync(job.pk, lambda *args: {"reference": "shared/copy", "size_bytes": image.size_bytes})
    assert finished.result_image.shared
    assert finished.result_image.owner_id is None


def test_sync_error_uses_selected_language_resolver_and_retains_code(transfer_data, monkeypatch):
    from django.test import Client
    from django.utils.translation import get_language
    from gpu_platform_control.core.api_auth import issue_token
    from gpu_platform_control.services import synchronization

    user, _, target, image = transfer_data
    target.architecture = "arm64"
    target.save(update_fields=["architecture"])
    resolved = []

    def resolve(code):
        resolved.append((code, get_language()))
        return "localized-message"

    monkeypatch.setattr(synchronization, "message", resolve)
    client = Client(HTTP_AUTHORIZATION="Bearer " + issue_token(user)["token"])
    for language in ("en", "zh-hans"):
        response = client.post(f"/api/v1/images/{image.pk}/sync", data={"target": target.pk},
                               content_type="application/json", HTTP_ACCEPT_LANGUAGE=language)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "image_architecture_mismatch"
    assert resolved == [("image_architecture_mismatch", "en"), ("image_architecture_mismatch", "zh-hans")]


def test_dataset_transfer_requires_admin_and_relative_paths(transfer_data):
    user, first, second, image = transfer_data
    user.is_staff = False
    with pytest.raises(PermissionDenied):
        request_dataset_sync(user, first, second)
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    for value in ["/etc", "../private", "a/../../private"]:
        with pytest.raises(ValidationError):
            request_dataset_sync(user, first, second, [value])
    job = request_dataset_sync(user, first, second, ["samples", "models/latest"])
    assert job.paths == ["samples", "models/latest"]


def test_rejected_import_keeps_reservation_until_cleanup_is_confirmed(transfer_data):
    from gpu_platform_control.services.synchronization import finish_cleanup

    user, first, second, image = transfer_data
    job = request_image_sync(user, image, second)
    result = {"id": "copied", "reference": "owner/copy", "size_bytes": 5 * GIB}

    def unavailable(*args):
        raise ConnectionError()

    pending = run_sync(job.pk, lambda *args: result, unavailable)
    assert pending.state == "cleaning"
    assert pending.cleanup_image["id"] == "copied"
    assert pending.reserved_bytes == 4 * GIB
    assert request_image_sync(user, image, second).pk == job.pk
    assert finish_cleanup(pending, lambda *args: None)
    pending.refresh_from_db()
    assert pending.state == "failed"
    assert not pending.cleanup_image


def test_uncertain_import_reuses_job_and_reservation(transfer_data):
    from gpu_platform_control.services.synchronization import UncertainImport

    user, first, second, image = transfer_data
    job = request_image_sync(user, image, second)

    def uncertain(*args):
        raise UncertainImport()

    retry = run_sync(job.pk, uncertain)
    assert retry.state == "queued"
    assert request_image_sync(user, image, second).pk == job.pk
    finished = run_sync(
        job.pk, lambda *args: {"id": "same-id", "reference": "owner/copy", "size_bytes": 4 * GIB}
    )
    assert finished.state == "completed"
    assert ImageArtifact.objects.filter(node=second, node_image_id="same-id").count() == 1


def test_configured_dataset_schedule_is_durable_and_interval_gated(transfer_data):
    from datetime import timedelta
    from django.test import override_settings
    from django.utils import timezone
    from gpu_platform_control.services.sync_policies import schedule_transfers
    from gpu_platform_control.slots.configuration import cluster_config

    user, source, target, image = transfer_data
    user.is_staff = user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    config = {
        "hosts": {
            "control": {"roles": ["control"]},
            "source": {"roles": ["node"]},
            "target": {"roles": ["node"]},
        },
        "control": {
            "sync": {
                "datasets": [
                    {"source": "source", "targets": ["target"], "paths": ["public"], "interval_seconds": 60}
                ]
            }
        },
    }
    with override_settings(PLATFORM_CLUSTER_CONFIG=config):
        cluster_config.cache_clear()
        try:
            now = timezone.now()
            jobs = schedule_transfers(now)
            assert len(jobs) == 1
            assert jobs[0].paths == ["public"]
            assert not schedule_transfers(now)
            run_sync(jobs[0].pk, lambda *args: {})
            assert not schedule_transfers(now + timedelta(seconds=30))
            assert len(schedule_transfers(now + timedelta(seconds=65))) == 1
        finally:
            cluster_config.cache_clear()


def test_image_accounting_includes_reserved_copies_during_other_operations(transfer_data):
    from gpu_platform_control.core.quotas.images import allocated_bytes

    user, first, second, image = transfer_data
    job = request_image_sync(user, image, second)
    assert allocated_bytes(user) == 8 * GIB
    SyncJob.objects.filter(pk=job.pk).update(state="cleaning")
    assert allocated_bytes(user) == 8 * GIB
    SyncJob.objects.filter(pk=job.pk).update(state="failed")
    assert allocated_bytes(user) == 4 * GIB


class BulkTransport:
    def __init__(self):
        self.uploads = []

    def receive_stream(self, endpoint, stream_id):
        yield b"archive"

    def send_stream(self, endpoint, stream_id, chunks):
        self.uploads.append((stream_id, b"".join(chunks)))


class BulkGateway:
    def __init__(self):
        self.transport = BulkTransport()
        self.pending_exports = 1
        self.pending_imports = 2
        self.uncertain_import = False
        self.commands = []
        self.import_complete = False

    def module(self, node, slot):
        return self.transport

    def endpoint(self, node):
        return node.name

    def request(self, node, operation, **payload):
        from gpu_platform_contracts.messages import Operation
        from gpu_platform_contracts.texts import Message
        from gpu_platform_control.slots.gateway import NodeCommandError

        self.commands.append((node.pk, operation, payload))
        if operation == Operation.IMAGE_EXPORT:
            if self.pending_exports:
                self.pending_exports -= 1
                raise NodeCommandError(Message(code="operation.pending"))
            return {"stream_id": "export-stream", "size_bytes": 7}
        if operation == Operation.IMAGE_IMPORT:
            if self.uncertain_import:
                raise ConnectionError()
            if self.pending_imports:
                self.pending_imports -= 1
                raise NodeCommandError(Message(code="operation.pending"))
            self.import_complete = True
            return {"id": payload["image_id"], "reference": "registry/copy", "size_bytes": 7}
        if operation == Operation.CLEANUP:
            assert self.import_complete
            return {}
        raise AssertionError(operation)


def test_bulk_transfer_polls_pending_operations_before_cleaning_streams(transfer_data):
    from gpu_platform_contracts.messages import Operation
    from gpu_platform_control.loop.transfers import Transfers

    user, source, target, image = transfer_data
    job = request_image_sync(user, image, target)
    gateway = BulkGateway()
    sleeps = []
    transfers = Transfers(gateway, sleep=sleeps.append)
    finished = run_sync(job.pk, transfers.transfer)
    assert finished.state == "completed"
    assert len(sleeps) == 3
    assert gateway.transport.uploads == [(job.pk.hex, b"archive")]
    exports = [payload for _, operation, payload in gateway.commands if operation == Operation.IMAGE_EXPORT]
    assert {payload["transfer_id"] for payload in exports} == {str(job.pk)}
    assert gateway.commands[-1] == (target.pk, Operation.CLEANUP, {"streams": [job.pk.hex]})


def test_uncertain_bulk_import_preserves_source_snapshot_and_reuses_target_stream(transfer_data):
    from gpu_platform_contracts.messages import Operation
    from gpu_platform_control.loop.transfers import Transfers

    user, source, target, image = transfer_data
    job = request_image_sync(user, image, target)
    gateway = BulkGateway()
    gateway.uncertain_import = True
    transfers = Transfers(gateway, sleep=lambda _: None)
    pending = run_sync(job.pk, transfers.transfer)
    assert pending.state == "queued"
    assert not any(operation == Operation.CLEANUP for _, operation, _ in gateway.commands)
    gateway.uncertain_import = False
    finished = run_sync(job.pk, transfers.transfer)
    assert finished.state == "completed"
    assert gateway.transport.uploads == [(job.pk.hex, b"archive")]


def test_pending_bulk_operation_has_a_deadline():
    from gpu_platform_contracts.messages import Operation
    from gpu_platform_control.slots.gateway import wait_for_result

    gateway = BulkGateway()
    gateway.pending_exports = 10
    elapsed = [0.0]

    def sleep(seconds):
        elapsed[0] += seconds

    with pytest.raises(TimeoutError):
        wait_for_result(
            gateway,
            ComputeNode(pk=1),
            Operation.IMAGE_EXPORT,
            timeout=2,
            clock=lambda: elapsed[0],
            sleep=sleep,
        )
    assert elapsed[0] == 2
    assert len(gateway.commands) == 3


def test_independent_node_image_with_same_reference_does_not_block_retention(transfer_data):
    from gpu_platform_control.core.image_lifecycle import in_use, request_image_deletion

    user, source, target, image = transfer_data
    ImageArtifact.objects.create(
        node=target,
        owner=user,
        name="local-copy",
        reference=image.reference,
        verified=True,
        architecture=image.architecture,
    )
    assert not in_use(image)
    request_image_deletion(image.pk, user)
    image.refresh_from_db()
    assert image.state == "deleting"
    ImageArtifact.objects.create(
        node=source,
        owner=user,
        name="same-node-alias",
        reference=image.reference,
        verified=True,
        architecture=image.architecture,
    )
    assert in_use(image)


class FileGateway:
    def __init__(self, source_node, target_node, tmp_path):
        from gpu_platform_node.commands import NodeEngine
        from gpu_platform_node.datasets import Datasets

        self.engines = {}
        self.sent = []
        self.fail_once = False
        self.exports = []
        for node in [source_node, target_node]:
            root = tmp_path / node.name / "data"
            root.mkdir(parents=True)
            self.engines[node.pk] = NodeEngine(
                node.name,
                "amd64",
                {},
                tmp_path / node.name / "state",
                {"dataset_sync": True},
                Datasets(root, node.name),
            )
        self.target = target_node.pk

    def module(self, node, slot):
        return self

    def endpoint(self, node):
        return node.pk

    def request(self, node, operation, **payload):
        from gpu_platform_contracts.messages import Command, Operation
        from gpu_platform_control.slots.gateway import NodeCommandError

        if operation == Operation.DATASETS_EXPORT:
            self.exports.append(dict(payload))
        result = self.engines[node.pk].handle(Command(operation=operation, payload=payload))
        if not result.ok:
            raise NodeCommandError(result.error)
        return result.data

    def receive_stream(self, endpoint, stream_id):
        yield from self.engines[endpoint].streams.send(stream_id)

    def send_stream(self, endpoint, stream_id, chunks):
        data = self.engines[endpoint].streams
        if self.fail_once and (data.receiver_directory / (stream_id + ".json")).exists():
            self.fail_once = False

            def interrupted():
                for chunk in chunks:
                    yield chunk[:2]
                    raise ConnectionError("connection lost during file")

            return data.receive(stream_id, interrupted())
        count = data.receive(stream_id, chunks)
        self.sent.append((endpoint, stream_id, count))
        return count


def test_dataset_sync_transfers_files_verifies_and_cleans_metadata(transfer_data, tmp_path):
    from gpu_platform_control.loop.transfers import Transfers

    user, source, target, _ = transfer_data
    user.is_staff = True
    gateway = FileGateway(source, target, tmp_path)
    sender, receiver = [gateway.engines[node.pk].datasets for node in [source, target]]
    (sender.root / "item").write_bytes(b"new-content")
    (receiver.root / "extra").write_bytes(b"old-data")
    for root in [sender.root, receiver.root]:
        (root / "same").write_bytes(b"unchanged")
    job = request_dataset_sync(user, source, target)
    finished = run_sync(job.pk, Transfers(gateway).transfer)
    assert finished.state == "completed"
    assert (receiver.root / "item").read_bytes() == b"new-content"
    assert not (receiver.root / "extra").exists()
    assert [row["path"] for row in gateway.exports] == ["item"]
    assert finished.transferred_bytes == len(b"new-contentunchanged")
    assert not receiver.transactions.exists()
    assert not any(gateway.engines[source.pk].streams.directory.iterdir())
    assert not any(gateway.engines[target.pk].streams.directory.iterdir())


def test_dataset_sync_resumes_only_missing_file_bytes_after_connection_loss(transfer_data, tmp_path):
    from gpu_platform_control.loop.transfers import Transfers

    user, source, target, _ = transfer_data
    user.is_staff = True
    gateway = FileGateway(source, target, tmp_path)
    sender, receiver = [gateway.engines[node.pk].datasets for node in [source, target]]
    (sender.root / "item").write_bytes(b"new-content")
    (receiver.root / "item").write_bytes(b"original")
    job = request_dataset_sync(user, source, target)
    gateway.fail_once = True
    pending = run_sync(job.pk, Transfers(gateway).transfer)
    assert pending.state == "queued"
    assert (receiver.root / "item").read_bytes() == b"original"
    finished = run_sync(job.pk, Transfers(gateway).transfer)
    assert finished.state == "completed"
    assert [row["offset"] for row in gateway.exports] == [0, 2]
    assert (receiver.root / "item").read_bytes() == b"new-content"
    assert not receiver.transactions.exists()


@pytest.mark.parametrize(
    "state,transferred,total,phase",
    [
        ("queued", 0, 0, "queued"),
        ("running", 0, 0, "preparing"),
        ("running", 0, 100, "preparing"),
        ("running", 25, 100, "transferring"),
        ("running", 25, 0, "transferring"),
        ("running", 100, 100, "applying"),
        ("completed", 100, 100, "completed"),
        ("failed", 25, 100, "failed"),
        ("cleaning", 100, 100, "cleaning"),
    ],
)
def test_sync_phase_distinguishes_preparation_transfer_and_application(
    transfer_data, state, transferred, total, phase
):
    from gpu_platform_control.services.read_models import materialize
    from gpu_platform_control.services.sync_api import sync_data

    user, source, target, _ = transfer_data
    job = SyncJob.objects.create(
        owner=user,
        source=source,
        target=target,
        kind="datasets",
        state=state,
        transferred_bytes=transferred,
        total_bytes=total,
    )
    assert job.phase == phase
    assert materialize(job)["phase"] == phase
    assert sync_data(job)["phase"] == phase


def test_file_transfer_limits_payload_bandwidth(transfer_data, tmp_path):
    import time
    from gpu_platform_control.loop.transfers import Transfers
    user, source, target, _ = transfer_data
    gateway = FileGateway(source, target, tmp_path)
    (gateway.engines[source.pk].datasets.root / 'item').write_bytes(b'123456')
    elapsed = [0.0]
    def sleep(seconds):
        elapsed[0] += seconds
        time.sleep(0.001)
    user.is_staff = True
    job = request_dataset_sync(user, source, target, bandwidth=2)
    result = run_sync(job.pk, Transfers(gateway, clock=lambda: elapsed[0], sleep=sleep).transfer)
    assert result.state == 'completed'
    assert elapsed[0] >= 3
