import json
import subprocess
from types import SimpleNamespace

import pytest

from gpu_platform_contracts.handoffs import VolumeRequest
from gpu_platform_contracts.assessment import ResourceIntent
from gpu_platform_contracts.module import ModuleContext, SlotPending
from gpu_platform_contracts.testing import StorageContract
from gpu_platform_contracts.testing.helpers import await_result
from gpu_platform_storage_rawfile.module import COPY_SCRIPT, Module


def test_pool_configuration_rejects_invalid_capacity_and_paths():
    valid = {"name": "data", "path": "/fixture", "storage_class": "data"}
    for change in ({"buffer_percent": 100}, {"buffer_percent": -1}, {"path": "relative"}, {"unknown": True}):
        with pytest.raises(ValueError):
            Module.validate_options({"pools": [{**valid, **change}]})


class VolumeBackend:
    def __init__(self):
        self.claims, self.jobs, self.pods = {}, {}, []
        self.persistent_volumes = {}
        self.deleted = []
        self.copy_complete = False

    def __call__(self, args, **kwargs):
        args = args[4:]
        result = None
        if args[:2] == ["get", "pods"]:
            result = {"items": self.pods}
        elif args[0] == "get":
            collection = {"pvc": self.claims, "pv": self.persistent_volumes, "job": self.jobs}[args[1]]
            result = collection.get(args[2])
            if args[1] == "job" and result and self.copy_complete:
                result["status"] = {"succeeded": 1}
        elif args[0] == "create":
            result = json.loads(kwargs["input"])
            result["metadata"].update(uid="uid-" + result["metadata"]["name"], resourceVersion="1")
            collection = self.claims if result["kind"] == "PersistentVolumeClaim" else self.jobs
            collection[result["metadata"]["name"]] = result
        elif args[0] == "delete":
            name = args[2].split("/")[-1]
            self.claims.pop(name)
            self.deleted.append(name)
        elif args[0] == "annotate":
            key, value = args[3].split("=", 1)
            self.claims[args[2]]["metadata"]["annotations"][key] = value
        elif args[0] == "patch":
            patch = json.loads(args[args.index("-p") + 1])
            self.claims[args[2]]["spec"].update(patch["spec"])
        return SimpleNamespace(stdout=json.dumps(result) if result is not None else "")


@pytest.fixture
def storage(tmp_path):
    backend = VolumeBackend()
    return Module(ModuleContext(node_id="node-a", state_directory=tmp_path), run=backend), backend


def test_creation_is_idempotent_and_owner_scoped(storage):
    module, backend = storage
    request = VolumeRequest(id="work-a", kind="work", mount_path="/workspace", size_gib=20)
    first = module.create("owner-a", request)
    assert module.create("owner-a", request) == first
    with pytest.raises(PermissionError):
        module.create("owner-b", request)
    assert len(backend.claims) == 1


def test_shrink_publishes_new_volume_after_successful_copy(storage):
    module, backend = storage
    source = module.create("owner-a", VolumeRequest(id="work-a", kind="work", mount_path="/workspace", size_gib=20))
    with pytest.raises(SlotPending):
        module.resize(source.id, source.owner, 10)
    assert module.get(source.id, source.owner).location == source.location
    assert backend.deleted == []
    backend.copy_complete = True
    resized = module.resize(source.id, source.owner, 10)
    assert resized.size_gib == 10
    assert resized.location != source.location
    assert backend.deleted == [source.location]
    assert module.get(source.id, source.owner) == resized


def test_busy_volume_cannot_shrink_or_delete(storage):
    module, backend = storage
    source = module.create("owner-a", VolumeRequest(id="work-a", kind="work", mount_path="/workspace", size_gib=20))
    backend.pods = [{"status": {"phase": "Running"}, "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": source.location}}]}}]
    with pytest.raises(SlotPending):
        module.resize(source.id, source.owner, 10)
    with pytest.raises(SlotPending):
        module.delete(source.id, source.owner)
    assert backend.deleted == []


def test_missing_owned_claim_is_reported_without_recreation(storage):
    module, backend = storage
    request = VolumeRequest(id="work-a", kind="work", mount_path="/workspace", size_gib=20)
    module.create("owner-a", request)
    backend.claims.clear()
    with pytest.raises(FileNotFoundError):
        module.create("owner-a", request)
    assert backend.claims == {}


def test_interrupted_resize_finishes_from_durable_transition(storage, monkeypatch):
    module, backend = storage
    source = module.create("owner-a", VolumeRequest(id="work-a", kind="work", mount_path="/workspace", size_gib=20))
    with pytest.raises(SlotPending):
        module.resize(source.id, source.owner, 10)
    backend.copy_complete = True
    original = module.command

    def interrupted(*args, **kwargs):
        if args[0] == "annotate":
            raise ConnectionError()
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "command", interrupted)
    with pytest.raises(ConnectionError):
        module.resize(source.id, source.owner, 10)
    assert len(backend.claims) == 2
    assert backend.deleted == []
    restarted = Module(module.context, run=backend)
    resized = restarted.resize(source.id, source.owner, 10)
    assert resized.size_gib == 10
    assert len(backend.claims) == 1
    assert "resize_transition" not in resized.metadata


def test_storage_usage_waits_for_physical_volume_cleanup(storage):
    module, backend = storage
    source = module.create("owner-a", VolumeRequest(id="work-a", kind="work", mount_path="/workspace", size_gib=20))
    claim = backend.claims[source.location]
    claim["spec"]["volumeName"] = "disk-a"
    backend.persistent_volumes["disk-a"] = {"spec": {"claimRef": {"uid": claim["metadata"]["uid"]}}}
    with pytest.raises(SlotPending):
        module.delete(source.id, source.owner)
    assert backend.claims == {}
    assert module.resources().used["storage"] == 20
    restarted = Module(module.context, run=backend)
    with pytest.raises(SlotPending):
        restarted.delete(source.id, source.owner)
    backend.persistent_volumes.clear()
    restarted.delete(source.id, source.owner)
    assert restarted.resources().used["storage"] == 0


def test_capacity_assessment_accounts_for_quota_without_creating_volumes(storage, monkeypatch):
    module, backend = storage
    module.context.options["pools"] = [{"name": "disk-fixture", "path": "/fixture", "storage_class": "data", "buffer_percent": 10}]
    monkeypatch.setattr("gpu_platform_storage_rawfile.module.os.statvfs", lambda path: SimpleNamespace(
        f_blocks=100, f_bavail=100, f_frsize=2**30))
    original = module.create("owner", VolumeRequest(id="home", kind="home", mount_path="/home/owner", size_gib=40))
    intent = ResourceIntent(owner="owner", volumes=[
        VolumeRequest(id="home", kind="home", mount_path="/home/owner", size_gib=40),
        VolumeRequest(id="work", kind="work", mount_path="/workspace", size_gib=50),
    ])
    assert module.assess(intent).feasible
    intent.volumes[1].size_gib = 51
    assessment = module.assess(intent)
    assert not assessment.feasible
    assert assessment.reason.code == "storage.capacity_unavailable"
    assert list(backend.claims) == [original.location]
    assert len(list(module.root.glob("*.json"))) == 1


def test_reported_capacity_excludes_disk_buffer_and_unavailable_space(storage, monkeypatch):
    module, _ = storage
    module.context.options["pools"] = [{"name": "disk-fixture", "path": "/fixture", "storage_class": "data", "buffer_percent": 10}]
    stats = SimpleNamespace(f_blocks=100, f_bavail=100, f_frsize=2**30)
    monkeypatch.setattr("gpu_platform_storage_rawfile.module.os.statvfs", lambda path: stats)
    module.create("owner", VolumeRequest(id="home", kind="home", mount_path="/home/owner", size_gib=40))
    assert module.resources().total["storage"] == 90
    stats.f_bavail = 20
    usage = module.resources()
    assert usage.used["storage"] == usage.used["home_storage"] == 40
    assert usage.total["storage"] - usage.used["storage"] == 10
    assert usage.used["work_storage"] == 0


class TestRawfileContract(StorageContract):
    @pytest.fixture
    def storage(self, tmp_path):
        class Backend(VolumeBackend):
            def __call__(self, args, **kwargs):
                result = super().__call__(args, **kwargs)
                if args[4] == "create":
                    obj = json.loads(kwargs["input"])
                    if obj["kind"] == "PersistentVolumeClaim":
                        (tmp_path / obj["metadata"]["name"]).mkdir()
                    else:
                        volumes = obj["spec"]["template"]["spec"]["volumes"]
                        source, target = [tmp_path / value["persistentVolumeClaim"]["claimName"] for value in volumes]
                        subprocess.run(["sh", "-c", COPY_SCRIPT, "copy", str(source), str(target)], check=True)
                return result

        backend = Backend()
        backend.copy_complete = True
        return Module(ModuleContext(node_id="node-a", state_directory=tmp_path), run=backend)

    @pytest.fixture
    def owner(self):
        return "contract-owner"

    @pytest.fixture
    def volume_request(self):
        return VolumeRequest(id="volume-a", kind="work", mount_path="/workspace", size_gib=2)

    @pytest.fixture
    def copy_request(self, volume_request):
        return volume_request.model_copy(update={"id": "volume-b"})

    @pytest.fixture
    def grown_size(self):
        return 3

    @pytest.fixture
    def write_volume(self, tmp_path):
        return lambda volume, data: (tmp_path / volume.location / "payload").write_bytes(data)

    @pytest.fixture
    def read_volume(self, tmp_path):
        return lambda volume: (tmp_path / volume.location / "payload").read_bytes()

    @pytest.fixture
    def await_operation(self):
        return lambda call: await_result(call, timeout=2, interval=0.01)
