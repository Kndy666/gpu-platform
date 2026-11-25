from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from gpu_platform_contracts.environment import EnvironmentIdentity
from gpu_platform_contracts.handoffs import GPUAllocation, GPUWorker, TaskInputs, Volume
from gpu_platform_contracts.messages import TaskSpec
from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.testing import RuntimeContract
from gpu_platform_runtime_k3s.client import Client
from gpu_platform_runtime_k3s.module import Module


class Cluster:
    check_owner = staticmethod(Client.check_owner)

    def __init__(self):
        self.objects = {}
        self.creations = 0
        self.available = True

    def get(self, kind, name):
        if not self.available:
            raise ConnectionError()
        return self.objects.get((kind, name))

    def list(self, resource, selector=None):
        if not self.available:
            raise ConnectionError()
        if resource == "nodes":
            return [{"status": {"allocatable": {"cpu": "8000m", "memory": "16Gi", "ephemeral-storage": "64Gi"}}}]
        return [obj for (kind, _), obj in self.objects.items() if kind == "Pod"]

    def ensure(self, obj):
        key = obj["kind"], obj["metadata"]["name"]
        if key not in self.objects:
            self.objects[key] = deepcopy(obj)
            self.creations += 1
        return self.objects[key]

    def delete(self, kind, name, owner):
        self.objects.pop((kind, name), None)

    def logs(self, name, tail):
        return ""


@pytest.fixture
def execution(tmp_path):
    task = TaskSpec(
        id="test-task",
        node_id="node-a",
        owner="user-a",
        kind="interactive",
        image="example.invalid/environment:latest",
        resources={"cpu": 2, "memory": 4, "system_storage": 3},
        identity=EnvironmentIdentity(username="member", uid=1000, gid=1000),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        login_port=31000,
    )
    inputs = TaskInputs(
        volumes=[
            Volume(
                id=kind,
                owner=task.owner,
                node_id=task.node_id,
                form="kubernetes-pvc",
                size_gib=10,
                location=kind,
                mount_path=path,
            )
            for kind, path in [("home", "/home/member"), ("work", "/workspace")]
        ]
    )
    cluster = Cluster()
    runtime = Module(ModuleContext(node_id=task.node_id, state_directory=tmp_path), client=cluster)
    return runtime, cluster, task, inputs


def test_repeated_start_reserves_once(execution):
    runtime, cluster, task, inputs = execution
    first = runtime.start(task, inputs)
    count = cluster.creations
    assert runtime.start(task, inputs) == first
    assert cluster.creations == count
    assert runtime.resources().used == {"cpu": 2, "memory": 4, "system_storage": 3}


def test_unknown_submission_state_does_not_reexecute(execution):
    runtime, cluster, task, inputs = execution
    runtime.start(task, inputs)
    cluster.objects.clear()
    count = cluster.creations
    assert runtime.start(task, inputs).state == "failed"
    assert cluster.creations == count


def test_unreachable_runtime_preserves_task_record(execution):
    runtime, cluster, task, inputs = execution
    runtime.start(task, inputs)
    cluster.available = False
    with pytest.raises(ConnectionError):
        runtime.status(task.id)
    cluster.available = True
    assert runtime.status(task.id).state == "queued"


def test_expiry_survives_process_restart(execution):
    runtime, cluster, task, inputs = execution
    runtime.start(task, inputs)
    restarted = Module(runtime.context, client=cluster)
    assert restarted.expire(task.expires_at - timedelta(seconds=1)) == []
    assert [status.id for status in restarted.expire(task.expires_at)] == [task.id]
    assert restarted.expire(task.expires_at) == []
    assert not cluster.objects


def test_stop_retains_resources_while_container_is_terminating(execution, monkeypatch):
    runtime, cluster, task, inputs = execution
    runtime.start(task, inputs)
    monkeypatch.setattr(cluster, "delete", lambda *args: None)
    status = runtime.stop(task.id)
    assert status.state in {"queued", "starting", "running"}
    assert status.usage == task.resources
    assert "terminal" not in runtime.read(task.id)
    cluster.objects.clear()
    assert runtime.stop(task.id).state == "ended"


def test_cross_owner_handoff_has_no_execution_side_effect(execution):
    runtime, cluster, task, inputs = execution
    inputs.volumes[0].owner = "another-user"
    with pytest.raises(PermissionError):
        runtime.start(task, inputs)
    assert cluster.creations == 0
    assert not runtime.record_path(task.id).exists()


@pytest.mark.parametrize("with_worker", [False, True])
def test_cpu_memory_limits_and_identity_apply_to_execution(execution, with_worker):
    runtime, cluster, task, inputs = execution
    if with_worker:
        inputs.gpu = GPUAllocation(
            id="gpu", task_id=task.id, node_id=task.node_id, form="nvidia-mps", devices=[],
            environment={"NVIDIA_VISIBLE_DEVICES": "void"},
            worker=GPUWorker(image="example.invalid/worker", client_directory="/client", ready_file="/tmp/ready",
                             environment={"NVIDIA_VISIBLE_DEVICES": "device-a", "PLATFORM_GPU_LEASE": "fixture"}),
        )
    runtime.start(task, inputs)
    pod = next(obj for (kind, _), obj in cluster.objects.items() if kind == "Pod")
    spec = pod["spec"]
    resources = spec["containers"][0]["resources"]
    assert resources["limits"] == resources["requests"]
    if with_worker:
        worker, = spec["initContainers"]
        assert worker["restartPolicy"] == "Always"
        assert worker["resources"]["requests"] == worker["resources"]["limits"]
        for key, suffix, total in (("cpu", "m", 2000), ("memory", "Mi", 4096), ("ephemeral-storage", "Mi", 3072)):
            assert sum(int(r[key].removesuffix(suffix)) for r in (resources["limits"], worker["resources"]["limits"])) == total
        workspace = spec["containers"][0]
        assert {item["name"]: item["value"] for item in workspace["env"]}["NVIDIA_VISIBLE_DEVICES"] == "void"
        assert "PLATFORM_GPU_LEASE" not in {item["name"] for item in workspace["env"]}
        assert any(mount["mountPath"] == "/client" and mount["readOnly"] for mount in workspace["volumeMounts"])
    else:
        assert resources["limits"]["cpu"] == str(task.resources["cpu"])
    assert spec["securityContext"]["runAsUser"] == task.identity.uid
    assert spec["automountServiceAccountToken"] is False
    assert {mount["mountPath"] for mount in spec["containers"][0]["volumeMounts"]} >= {
        "/home/member",
        "/workspace",
    }


def test_interrupted_submission_finishes_from_durable_intent(execution, monkeypatch):
    runtime, cluster, task, inputs = execution
    original = cluster.ensure

    def interrupt(obj):
        if obj["kind"] == "Service":
            raise ConnectionError()
        return original(obj)

    monkeypatch.setattr(cluster, "ensure", interrupt)
    with pytest.raises(ConnectionError):
        runtime.start(task, inputs)
    assert len(cluster.objects) == 1
    monkeypatch.setattr(cluster, "ensure", original)
    restarted = Module(runtime.context, client=cluster)
    assert restarted.status(task.id).state == "queued"
    assert len(cluster.objects) == 3
    assert "preparing" not in restarted.read(task.id)


def test_stop_during_partial_submission_does_not_create_work(execution, monkeypatch):
    runtime, cluster, task, inputs = execution
    original = cluster.ensure

    def interrupt(obj):
        if obj["kind"] == "Service":
            raise ConnectionError()
        return original(obj)

    monkeypatch.setattr(cluster, "ensure", interrupt)
    with pytest.raises(ConnectionError):
        runtime.start(task, inputs)
    assert runtime.stop(task.id).state == "ended"
    assert cluster.creations == 1
    assert not cluster.objects


def test_replacement_workload_is_not_adopted(execution):
    runtime, cluster, task, inputs = execution
    runtime.start(task, inputs)
    for (kind, _), obj in cluster.objects.items():
        if kind == "Pod":
            obj["metadata"]["annotations"]["platform.task"] = "different-task"
    with pytest.raises(PermissionError):
        runtime.status(task.id)


def test_ambiguous_workload_submission_is_not_replayed(execution, monkeypatch):
    runtime, cluster, task, inputs = execution
    original = cluster.ensure
    attempts = []

    def interrupt(obj):
        if obj["kind"] == "Pod":
            attempts.append(obj)
            raise ConnectionError()
        return original(obj)

    monkeypatch.setattr(cluster, "ensure", interrupt)
    with pytest.raises(ConnectionError):
        runtime.start(task, inputs)
    assert runtime.status(task.id).state == "failed"
    assert len(attempts) == 1


def test_lost_workload_reply_uses_observed_workload(execution, monkeypatch):
    runtime, cluster, task, inputs = execution
    original = cluster.ensure

    def interrupt(obj):
        result = original(obj)
        if obj["kind"] == "Pod":
            raise ConnectionError()
        return result

    monkeypatch.setattr(cluster, "ensure", interrupt)
    with pytest.raises(ConnectionError):
        runtime.start(task, inputs)
    assert runtime.status(task.id).state == "queued"
    assert cluster.creations == 3


def test_registry_ca_and_reader_credentials_mount_in_environment(execution):
    from gpu_platform_contracts.handoffs import Image

    runtime, cluster, task, inputs = execution
    inputs.image = Image(
        id="image",
        owner="publisher",
        node_id=task.node_id,
        form="oci-registry",
        reference="registry.invalid:5000/image:tag",
        architecture="amd64",
        credentials={"server": "registry.invalid:5000", "username": "reader", "password": "secret"},
        metadata={"ca_certificate": "fixture-certificate"},
    )
    runtime.start(task, inputs)
    pod = next(obj for (kind, _), obj in cluster.objects.items() if kind == "Pod")
    mounts = {item["name"]: item for item in pod["spec"]["containers"][0]["volumeMounts"]}
    assert mounts["registry-ca"]["mountPath"] == "/etc/docker/certs.d/registry.invalid:5000"
    assert mounts["registry-ca"]["readOnly"] and mounts["registry-auth"]["readOnly"]
    secret = next(
        obj
        for (kind, _), obj in cluster.objects.items()
        if kind == "Secret" and obj.get("type") == "kubernetes.io/dockerconfigjson"
    )
    assert set(secret["stringData"]) == {".dockerconfigjson", "ca.crt"}
    runtime.stop(task.id)
    assert not cluster.objects


def test_installed_local_catalog_survives_runtime_restart(execution):
    import json

    runtime, cluster, task, inputs = execution
    catalog = runtime.context.state_directory / "bootstrap" / "catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        json.dumps(
            [{"id": "environment", "reference": "example.invalid/environment:tag", "form": "node-local"}]
        )
    )
    first = runtime.images()
    restarted = Module(runtime.context, client=cluster)
    assert first == restarted.images()
    assert first[0].node_id == task.node_id and first[0].credentials == {}


def test_assessment_counts_reservations_without_advancing_pending_execution(execution):
    from gpu_platform_contracts.assessment import ResourceIntent

    runtime, cluster, task, inputs = execution
    objects = __import__("gpu_platform_runtime_k3s.manifests", fromlist=["task_objects"]).task_objects(
        task, inputs, {}
    )
    runtime.save(task.id, {"task": task.model_dump(mode="json"), "preparing": objects, "objects": []})
    assert runtime.assess(ResourceIntent(resources={"cpu": 6, "memory": 12})).feasible
    assert not runtime.assess(ResourceIntent(resources={"cpu": 7, "memory": 12})).feasible
    assert cluster.creations == 0


def test_terminal_logs_remain_available_after_pod_cleanup(execution, monkeypatch):
    runtime, cluster, task, inputs = execution
    runtime.start(task, inputs)
    monkeypatch.setattr(cluster, "logs", lambda name, tail: "first\nsecond\nthird")
    runtime.stop(task.id)
    assert not cluster.objects
    restarted = Module(runtime.context, client=cluster)
    assert restarted.logs(task.id, 2).splitlines() == ["second", "third"]
    restarted.acknowledge(task.id)
    restarted.acknowledge(task.id)
    assert restarted.list_tasks() == []
    assert restarted.status(task.id).state == "ended"
    assert restarted.logs(task.id, 1) == "third"


def test_snapshot_release_is_confined_and_repeatable(execution):
    runtime, _, _, _ = execution
    identifier = "a" * 32
    archive = runtime.root / (identifier + ".oci.tar")
    archive.write_bytes(b"artifact")
    runtime.release_snapshot(identifier)
    runtime.release_snapshot(identifier)
    assert not archive.exists()
    with pytest.raises(ValueError):
        runtime.release_snapshot("../outside")


@pytest.fixture
def runtime(execution):
    return execution[0]


@pytest.fixture
def task_spec(execution):
    return execution[2]


@pytest.fixture
def task_inputs(execution, tmp_path):
    from gpu_platform_contracts.handoffs import ReadOnlyDirectory

    inputs = execution[3]
    inputs.datasets = [
        ReadOnlyDirectory(
            node_id=execution[2].node_id, form="local-directory", path=str(tmp_path), mount_path="/datasets"
        )
    ]
    return inputs


@pytest.fixture
def await_task(execution):
    def wait(runtime, identifier, states):
        if "running" in states:
            for (kind, _), obj in execution[1].objects.items():
                if kind == "Pod":
                    obj["status"] = {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}
        result = runtime.status(identifier)
        assert result.state in states
        return result

    return wait


@pytest.fixture
def environment_probe(execution):
    def probe(runtime, identifier):
        from pathlib import Path
        from gpu_platform_runtime_k3s import module

        pod = next(obj for (kind, _), obj in execution[1].objects.items() if kind == "Pod")
        spec = pod["spec"]
        container = spec["containers"][0]
        mounts = {item["mountPath"]: item for item in container["volumeMounts"]}
        ssh = any(port["containerPort"] == 2222 for port in container["ports"])
        return {
            "ssh": ssh,
            "vscode": ssh and "/run/platform" in mounts,
            "isolated_identity": spec["securityContext"]["runAsNonRoot"]
            and not spec["automountServiceAccountToken"],
            "home_directory": "/home/member" in mounts,
            "work_directory": "/workspace" in mounts,
            "docker": (Path(module.__file__).parent / "images/docker.sh").is_file()
            and container["securityContext"]["allowPrivilegeEscalation"],
            "visible_resource_limits": all(
                path in mounts
                for path in ("/proc/cpuinfo", "/proc/meminfo", "/sys/devices/system/cpu/online")
            )
            and bool(container["resources"]["limits"]),
            "read_only_public_data": mounts["/datasets"]["readOnly"],
        }

    return probe


class TestK3sRuntimeContract(RuntimeContract):
    pass
