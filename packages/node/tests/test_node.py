import io
import json
import socket
import threading
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gpu_platform_contracts.environment import EnvironmentIdentity
from gpu_platform_contracts.handoffs import GPUAllocation, Image, Volume
from gpu_platform_contracts.messages import (
    Command,
    Operation,
    Reply,
    TaskSpec,
    TaskStatus,
    encode_message,
    read_message,
)
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.resources import GPURequest, ResourceUsage
from gpu_platform_contracts.texts import Message
from gpu_platform_contracts.testing.helpers import await_result
from gpu_platform_node.commands import NodeEngine
from gpu_platform_node.daemon import Server
from gpu_platform_node.datasets import Datasets
from gpu_platform_node.sync import Streams, relay


class Runtime:
    descriptor = ModuleDescriptor(name="fixture-runtime", slot="runtime", version="1", side="node")

    def __init__(self):
        self.tasks, self.inputs = {}, {}
        self.fail = False
        self.acknowledged = set()

    def start(self, task, inputs):
        if self.fail:
            raise RuntimeError("injected")
        self.tasks[task.id] = TaskStatus(id=task.id, state="running", usage=task.resources)
        self.inputs[task.id] = inputs
        return self.tasks[task.id]

    def status(self, task_id):
        return self.tasks[task_id]

    def stop(self, task_id):
        self.tasks[task_id] = TaskStatus(id=task_id, state="ended")
        return self.tasks[task_id]

    def list_tasks(self):
        return [task for task in self.tasks.values() if task.id not in self.acknowledged]

    def acknowledge(self, task_id):
        self.acknowledged.add(task_id)

    def resources(self):
        return ResourceUsage(total={"arbitrary.capacity": 8}, used={"arbitrary.capacity": 1})

    def images(self):
        return [
            Image(
                id="source",
                reference="source",
                form="node-local",
                owner="shared",
                node_id="node-a",
                architecture="amd64",
            )
        ]

    def expire(self, now):
        return []

    def extend(self, task_id, expires_at):
        return self.status(task_id)

    def set_authorized_keys(self, task_id, keys):
        self.keys = keys


class Storage:
    descriptor = ModuleDescriptor(name="fixture-storage", slot="storage", version="1", side="node")

    def create(self, owner, request):
        return Volume(
            id=request.id,
            owner=owner,
            node_id="node-a",
            form="directory",
            size_gib=request.size_gib,
            location="/isolated",
            mount_path=request.mount_path,
        )

    def resources(self):
        return ResourceUsage()


class GPU:
    descriptor = ModuleDescriptor(name="fixture-gpu", slot="gpu", version="1", side="node")

    def __init__(self):
        self.leases = {}
        self.closing = set()

    def allocate(self, task_id, requests):
        allocation = GPUAllocation(
            id=task_id, task_id=task_id, node_id="node-a", form="fixture", devices=requests
        )
        self.leases[task_id] = allocation
        return allocation

    def release(self, allocation_id):
        self.leases.pop(allocation_id, None)
        self.closing.discard(allocation_id)

    def reconcile(self, task_id, *, stopping=False):
        if stopping:
            self.closing.add(task_id)

    def lookup(self, task_id):
        return self.leases.get(task_id)

    def resources(self):
        return ResourceUsage()

    def inventory(self):
        return []

    def explanation(self):
        return Message(code="gpu.fixture_allocation", parameters={"minimum": 10})


@pytest.fixture
def task_spec():
    return TaskSpec(
        id="task-a",
        node_id="node-a",
        owner="owner-a",
        kind="interactive",
        image="source",
        resources={"arbitrary.capacity": 1},
        identity=EnvironmentIdentity(username="member", uid=1000, gid=1000),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def engine(tmp_path):
    return NodeEngine("node-a", "amd64", {"runtime": Runtime(), "storage": Storage(), "gpu": GPU()}, tmp_path)


@pytest.mark.parametrize("wire_size,valid", [("1.25", True), ("-1", False), ("NaN", False)])
def test_resize_validates_wire_quantity_before_invoking_storage(engine, wire_size, valid):
    received = []
    engine.slots["storage"].resize = lambda identifier, owner, size: received.append((identifier, owner, size))
    command = Command(operation=Operation.STORAGE_RESIZE,
                      payload={"volume_id": "volume", "owner": "member", "size_gib": wire_size})
    decoded = Command.model_validate(read_message(io.BytesIO(encode_message(command))))
    assert engine.handle(decoded).ok == valid
    assert received == ([("volume", "member", Decimal("1.25"))] if valid else [])


def test_dispatch_owns_handoff_and_lifecycle(engine, task_spec):
    task_spec.gpus = [GPURequest(device_id="device-a", compute_percent=25, memory_gib=2)]
    reply = engine.handle(Command(operation=Operation.TASK_START, payload=task_spec.model_dump(mode="json")))
    assert reply.ok and reply.data["state"] == "running"
    assert engine.slots["runtime"].inputs[task_spec.id].gpu.devices == task_spec.gpus
    assert engine.start(task_spec).id == task_spec.id
    stopped = engine.handle(Command(operation=Operation.TASK_STOP, payload={"task_id": task_spec.id}))
    assert stopped.ok and not engine.slots["gpu"].leases


def test_failed_start_releases_gpu(engine, task_spec):
    task_spec.gpus = [GPURequest(device_id="device-a", compute_percent=25, memory_gib=2)]
    engine.slots["runtime"].fail = True
    reply = engine.handle(Command(operation=Operation.TASK_START, payload=task_spec.model_dump(mode="json")))
    assert not reply.ok
    assert not engine.slots["gpu"].leases


def test_node_identity_and_maintenance_guard_dispatch(engine, task_spec):
    engine.handle(Command(operation=Operation.MAINTENANCE, payload={"enabled": True}))
    assert not engine.handle(
        Command(operation=Operation.TASK_START, payload=task_spec.model_dump(mode="json"))
    ).ok
    engine.handle(Command(operation=Operation.MAINTENANCE, payload={"enabled": False}))
    task_spec.node_id = "other"
    assert not engine.handle(
        Command(operation=Operation.TASK_START, payload=task_spec.model_dump(mode="json"))
    ).ok
    assert engine.slots["runtime"].tasks == {}


def test_stop_command_correlates_reply_and_updates_task_state(engine, task_spec):
    engine.start(task_spec)
    command = Command(version="99.0", operation=Operation.TASK_STOP, payload={"task_id": task_spec.id})
    reply = engine.handle(command)
    assert reply.ok and reply.id == command.id
    assert engine.slots["runtime"].status(task_spec.id).state == "ended"


def test_expiry_works_after_engine_restart(engine, task_spec, tmp_path):
    engine.start(task_spec)
    engine.state["tasks"][task_spec.id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    engine.save()
    restarted = NodeEngine("node-a", "amd64", engine.slots, tmp_path)
    restarted.tick()
    assert engine.slots["runtime"].status(task_spec.id).state == "ended"


def test_deadline_and_key_updates_persist(engine, task_spec, tmp_path):
    engine.start(task_spec)
    expiry = task_spec.expires_at + timedelta(hours=1)
    assert engine.handle(
        Command(
            operation=Operation.TASK_EXTEND,
            payload={"task_id": task_spec.id, "expires_at": expiry.isoformat()},
        )
    ).ok
    assert engine.handle(
        Command(operation=Operation.TASK_KEYS, payload={"task_id": task_spec.id, "keys": []})
    ).ok
    restarted = NodeEngine("node-a", "amd64", engine.slots, tmp_path)
    stored = TaskSpec.model_validate(restarted.state["tasks"][task_spec.id])
    assert stored.expires_at == expiry and stored.identity.ssh_keys == []


def test_dataset_sync_overwrites_only_selected_tree(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    (source / "selected").mkdir(parents=True)
    (source / "selected" / "item").write_bytes(b"\x00\xff")
    (target / "selected").mkdir(parents=True)
    (target / "selected" / "stale").write_bytes(b"old")
    (target / "untouched").write_bytes(b"keep")
    sender, receiver = Datasets(source, "one"), Datasets(target, "two")
    manifest = sender.manifest(["selected"])
    receiver.begin("transfer", io.BytesIO(sender.encode_manifest(manifest)), ["selected"])
    receiver.prepare_file("transfer", "selected/item")
    receiver.receive_file("transfer", "selected/item", 0, [b"\x00\xff"])
    assert receiver.verify_file("transfer", "selected/item")
    receiver.finish("transfer")
    assert (target / "selected" / "item").read_bytes() == b"\x00\xff"
    assert not (target / "selected" / "stale").exists()
    assert (target / "untouched").read_bytes() == b"keep"


def test_dataset_rejects_traversal_before_commit(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep").write_bytes(b"retain")
    manifest = {"version":1, "paths":["."], "entries":{"../escaped":{"kind":"directory", "modified":0}}}
    with pytest.raises(ValueError):
        Datasets(target, "two").begin("transfer", io.BytesIO(json.dumps(manifest).encode()), [])
    assert (target / "keep").read_bytes() == b"retain"
    assert not (tmp_path / "escaped").exists()


def test_stream_atomicity_and_limit(tmp_path):
    streams = Streams(tmp_path, maximum_bytes=4)
    streams.receive("transfer", [b"one"])
    with pytest.raises(ValueError):
        streams.receive("transfer", [b"three"])
    assert b"".join(streams.send("transfer")) == b"one"
    with pytest.raises(ValueError):
        streams.path("../escape")


def test_relay_rate_and_progress():
    elapsed, progress = [0.0], []

    def sleep(seconds):
        elapsed[0] += seconds

    result = list(relay([b"1234", b"56"], 2, progress.append, lambda: elapsed[0], sleep))
    assert b"".join(result) == b"123456"
    assert elapsed[0] == 3 and progress == [4, 6]


def test_socket_commands_and_binary_stream(engine, tmp_path):
    socket_path = tmp_path / "node.sock"
    with Server(socket_path, engine) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(socket_path))
                command = Command(operation=Operation.STATUS)
                connection.sendall(encode_message(command))
                with connection.makefile("rb") as stream:
                    reply = Reply.model_validate(read_message(stream))
                assert reply.ok and reply.id == command.id
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(socket_path))
                connection.sendall(
                    b'{"channel":"stream","direction":"upload","stream_id":"binary"}\n' + bytes(range(256))
                )
                connection.shutdown(socket.SHUT_WR)
                with connection.makefile("rb") as stream:
                    assert Reply.model_validate(read_message(stream)).ok
            assert b"".join(engine.streams.send("binary")) == bytes(range(256))
        finally:
            server.shutdown()
            thread.join()


def test_gpu_release_waits_for_runtime_cleanup(engine, task_spec):
    task_spec.gpus = [GPURequest(device_id="device-a", compute_percent=25, memory_gib=2)]
    engine.start(task_spec)
    runtime = engine.slots["runtime"]
    runtime.tasks[task_spec.id].state = "ended"
    original_stop = runtime.stop
    runtime.stop = lambda task_id: TaskStatus(id=task_id, state="running")
    engine.tick()
    assert engine.slots["gpu"].closing == {task_spec.id}
    assert task_spec.id in engine.slots["gpu"].leases
    runtime.stop = original_stop
    engine.tick()
    assert not engine.slots["gpu"].closing and not engine.slots["gpu"].leases


def test_unknown_runtime_state_preserves_allocations(engine, task_spec):
    task_spec.gpus = [GPURequest(device_id="device-a", compute_percent=25, memory_gib=2)]
    engine.start(task_spec)

    def disconnected():
        raise ConnectionError()

    engine.slots["runtime"].list_tasks = disconnected
    with pytest.raises(ConnectionError):
        engine.tick()
    assert task_spec.id in engine.slots["gpu"].leases


def test_duplicate_dataset_selection_does_not_delete_imported_data(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    (source / "selected").mkdir(parents=True)
    (source / "selected" / "item").write_bytes(b"\x01\x02")
    sender, receiver = Datasets(source, "a"), Datasets(target, "b")
    manifest = sender.manifest(["selected", "selected"])
    receiver.begin("transfer", io.BytesIO(sender.encode_manifest(manifest)), ["selected", "selected"])
    receiver.prepare_file("transfer", "selected/item")
    receiver.receive_file("transfer", "selected/item", 0, [b"\x01\x02"])
    assert receiver.verify_file("transfer", "selected/item")
    receiver.finish("transfer")
    assert (target / "selected" / "item").read_bytes() == b"\x01\x02"


def test_dataset_import_rejects_destination_symlinks(tmp_path):
    target = tmp_path / "target"
    (target / "actual").mkdir(parents=True)
    (target / "selected").symlink_to(target / "actual")
    with pytest.raises(ValueError):
        document={"version":1,"paths":["selected"],"entries":{}}
        Datasets(target, "a").begin("transfer", io.BytesIO(json.dumps(document).encode()), ["selected"])
    assert (target / "actual").is_dir()


def test_runtime_catalog_drives_registry_free_dispatch(engine, task_spec):
    capability = engine.capabilities()
    assert capability.gpu_explanation.parameters == {"minimum": 10}
    assert capability.images[0].form == "node-local"
    assert capability.images[0].credentials == {}
    engine.start(task_spec)
    assert engine.slots["runtime"].inputs[task_spec.id].image.id == capability.images[0].id


def test_terminal_build_access_revoked_after_node_restart(engine, tmp_path):
    from gpu_platform_contracts.slots.build import BuildResult

    released = []

    class Builder:
        def status(self, build_id):
            return BuildResult(id=build_id, state="failed")

        def release_result(self, build_id):
            pass

    class Registry:
        def release_build_access(self, build_id):
            released.append(build_id)

    engine.slots.update(build=Builder(), registry=Registry())
    engine.state["builds"]["build-a"] = {"owner": "owner-a"}
    engine.save()
    restarted = NodeEngine("node-a", "amd64", engine.slots, tmp_path)
    restarted.tick()
    assert released == ["build-a"] and restarted.state["builds"] == {}


def test_assessment_combines_slot_quantities_without_reserving(engine):
    from gpu_platform_contracts.assessment import ResourceAssessment, ResourceIntent

    engine.slots["runtime"].assess = lambda intent: ResourceAssessment(feasible=True, quantities={"cpu": 2})
    engine.slots["storage"].assess = lambda intent: ResourceAssessment(
        feasible=True, quantities={"work_storage": 4}
    )
    engine.slots["gpu"].assess = lambda intent: ResourceAssessment(
        feasible=True,
        quantities={"gpu_compute": 25},
        gpus=[GPURequest(device_id="resolved", compute_percent=25, memory_gib=1)],
    )
    intent = ResourceIntent(
        resources={"cpu": 2}, gpus=[GPURequest(device_id="auto:0", compute_percent=25, memory_gib=1)]
    )
    result = engine.assess(intent)
    assert result.feasible and result.quantities == {"cpu": 2, "work_storage": 4, "gpu_compute": 25}
    assert result.gpus[0].device_id == "resolved"
    assert not engine.slots["runtime"].tasks and not engine.slots["gpu"].leases


def completed(engine, command):
    return await_result(
        lambda: engine.handle(command),
        ready=lambda reply: reply.ok or reply.error.code != "operation.pending",
        timeout=3,
        interval=0.01,
    )


def test_expiry_runs_during_blocked_export_and_retries_reuse_archive(engine, task_spec, tmp_path):
    entered, proceed, ticked = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def export(image_id, owner):
        calls.append((image_id, owner))
        entered.set()
        assert proceed.wait(timeout=3)
        yield b"verified-payload"

    engine.features["image_sync"] = True
    engine.slots["registry"] = SimpleNamespace(export_image=export)
    task_spec.gpus = [GPURequest(device_id="device-a", compute_percent=25, memory_gib=2)]
    engine.start(task_spec)
    engine.state["tasks"][task_spec.id]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    command = Command(
        operation=Operation.IMAGE_EXPORT,
        payload={"image_id": "image", "owner": "owner", "transfer_id": "attempt"},
    )
    assert engine.handle(command).error.code == "operation.pending"
    assert entered.wait(timeout=1)
    assert engine.handle(command).error.code == "operation.pending"
    worker = threading.Thread(target=lambda: (engine.tick(), ticked.set()), daemon=True)
    worker.start()
    try:
        assert ticked.wait(timeout=1)
        assert not engine.slots["gpu"].leases
    finally:
        proceed.set()
        worker.join(timeout=2)
    first = completed(engine, command)
    assert first.ok and calls == [("image", "owner")]
    restarted = NodeEngine("node-a", "amd64", engine.slots, tmp_path, engine.features)
    assert restarted.handle(command).data == first.data
    engine.streams.remove(first.data["stream_id"])
    second = completed(restarted, command)
    assert second.ok and second.data["stream_id"] != first.data["stream_id"]
    assert len(calls) == 2


def test_import_retry_after_input_removal_and_restart_returns_committed_result(engine, tmp_path):
    calls = []
    image = Image(
        id="imported",
        owner="owner",
        node_id="node-a",
        architecture="amd64",
        form="fixture",
        reference="reference",
    )

    def import_image(image_id, owner, architecture, chunks):
        calls.append(b"".join(chunks))
        return image

    engine.features["image_sync"] = True
    engine.slots["registry"] = SimpleNamespace(import_image=import_image)
    engine.streams.receive("transfer", [b"binary"])
    command = Command(
        operation=Operation.IMAGE_IMPORT,
        payload={
            "image_id": image.id,
            "owner": image.owner,
            "architecture": "amd64",
            "stream_id": "transfer",
        },
    )
    first = completed(engine, command)
    assert first.ok and not engine.streams.path("transfer").exists()
    restarted = NodeEngine("node-a", "amd64", engine.slots, tmp_path, engine.features)
    assert restarted.handle(command).data == first.data
    assert calls == [b"binary"]


def test_dataset_export_limit_leaves_no_partial_stream(engine, tmp_path):
    source = tmp_path / "datasets"
    source.mkdir()
    (source / "sample").write_bytes(b"data"*10)
    engine.datasets = Datasets(source, "node-a")
    engine.features["dataset_sync"] = True
    engine.streams.maximum_bytes = 20
    reply = engine.handle(Command(operation=Operation.DATASETS_EXPORT, payload={"path":"sample", "size_bytes":40, "modified":(source/"sample").stat().st_mtime}))
    assert not reply.ok
    assert list(engine.streams.directory.iterdir()) == []


def test_reports_durable_byte_progress_and_failed_streams(engine, tmp_path):
    engine.streams.receive("transfer", [b"one", b"two"])
    report = engine.report()
    assert report.sync[0].state == "completed" and report.sync[0].transferred_bytes == 6
    restarted = NodeEngine(engine.node_id, engine.architecture, engine.slots, tmp_path)
    assert restarted.report().sync == report.sync
    with pytest.raises(OSError):

        def failing():
            yield b"partial"
            raise OSError("fixture")

        engine.streams.receive("failed", failing())
    failed = next(item for item in engine.report().sync if item.id == "failed")
    assert failed.state == "failed" and failed.transferred_bytes == 7


def test_committed_build_result_survives_cleanup_and_does_not_reexecute(engine, tmp_path):
    from gpu_platform_contracts.slots.build import BuildResult

    calls, released = [], []
    source = Image(
        id="build",
        owner="owner",
        node_id="node-a",
        architecture="amd64",
        form="oci-archive",
        reference="archive",
    )
    stored = source.model_copy(update={"form": "oci-registry", "reference": "registry/image"})
    builder = SimpleNamespace(
        build=lambda spec: calls.append(spec.id) or BuildResult(id=spec.id, state="completed", image=source),
        release_result=released.append,
    )
    engine.slots.update(
        build=builder,
        registry=SimpleNamespace(store=lambda *args: stored, release_build_access=lambda *args: None),
    )
    payload = {"id": "build", "owner": "owner", "architecture": "amd64", "description": "FROM scratch"}
    first = engine.execute(Operation.IMAGE_BUILD, payload)
    restarted = NodeEngine("node-a", "amd64", engine.slots, tmp_path)
    second = restarted.execute(Operation.IMAGE_BUILD, payload)
    assert first == second and calls == ["build"]
    assert released == ["build", "build"]


def test_gpu_lease_recovered_after_crash_before_node_journal_write(engine, task_spec, tmp_path):
    task_spec.gpus = [GPURequest(device_id="device-a", compute_percent=25, memory_gib=2)]
    engine.state["tasks"][task_spec.id] = task_spec.model_dump(mode="json")
    engine.save()
    engine.slots["gpu"].allocate(task_spec.id, task_spec.gpus)
    assert task_spec.id not in engine.state["allocations"]
    restarted = NodeEngine("node-a", "amd64", engine.slots, tmp_path)
    restarted.tick()
    assert not engine.slots["gpu"].leases
    assert not restarted.state["allocations"]


def test_terminal_ack_removes_task_from_reports_without_losing_query(engine, task_spec):
    engine.start(task_spec)
    command = Command(operation=Operation.TASK_ACK, payload={"ids": [task_spec.id]})
    assert not engine.handle(command).ok
    engine.execute(Operation.TASK_STOP, {"task_id": task_spec.id})
    assert engine.report().tasks
    assert engine.handle(command).ok
    assert engine.handle(command).ok
    assert engine.report().tasks == []
    assert task_spec.id not in engine.state["tasks"]
    assert engine.execute(Operation.TASK_STATUS, {"task_id": task_spec.id}).state == "ended"


def test_accelerator_slot_receives_scalar_resource_intent(engine):
    from gpu_platform_contracts.assessment import ResourceIntent, ResourceAssessment
    intent = ResourceIntent(resources={"accelerator_count":1})
    for name in ["runtime","storage"]:
        engine.slots[name].assess = lambda supplied: ResourceAssessment(feasible=True)
    received=[]
    engine.slots["gpu"].assess = lambda supplied: (received.append(supplied) or ResourceAssessment(feasible=True))
    assert engine.assess(intent).feasible
    assert received == [intent]


def test_file_download_range_uses_transport_without_staging_payload(engine, tmp_path):
    from gpu_platform_contracts.module import ModuleContext
    from gpu_platform_transport_local.module import Module
    path = tmp_path / 'source-file'
    payload = bytes(range(256)) * 10000
    path.write_bytes(payload)
    engine.streams.file_source('file-range', path, 4100)
    socket_path = tmp_path / 'node.sock'
    transport = Module(ModuleContext(node_id=engine.node_id, state_directory=tmp_path,
        options={'socket_path': str(socket_path)}))
    with Server(socket_path, engine) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert b''.join(transport.receive_stream('local', 'file-range')) == payload[4100:]
            assert not list(engine.streams.directory.iterdir())
        finally:
            server.shutdown()
            thread.join()


def test_changed_file_source_is_rejected_before_download(engine, tmp_path):
    path = tmp_path / 'item'
    path.write_bytes(b'first')
    engine.streams.file_source('file-range', path)
    path.write_bytes(b'second')
    with pytest.raises(ValueError):
        with engine.streams.download('file-range'):
            pytest.fail('mutated source accepted')
