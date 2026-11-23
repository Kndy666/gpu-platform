import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from gpu_platform_contracts.handoffs import Image, PortConnection, Snapshot
from gpu_platform_contracts.peer_ssh import FEATURE, SSH_PORT
from gpu_platform_contracts.assessment import ResourceAssessment
from gpu_platform_contracts.messages import ACTIVE_STATES, TaskSpec, TaskStatus
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.resources import ResourceDefinition, ResourceUsage
from gpu_platform_contracts.slots.runtime import Runtime
from gpu_platform_contracts.texts import LocalizedText, Message

from .client import Client
from .manifests import name_for, task_objects
from .options import Options
from .oci import sanitize_snapshot


def quantity(value):
    units: dict[str, int | Decimal] = {
        "Ki": 2**10,
        "Mi": 2**20,
        "Gi": 2**30,
        "Ti": 2**40,
        "m": Decimal("0.001"),
    }
    value = str(value)
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            return Decimal(value[: -len(suffix)]) * multiplier
    return Decimal(value)


class Module(Runtime):
    options_model = Options
    @classmethod
    def check_host(cls, context):
        from .install import check_host

        return check_host(context)

    @classmethod
    def install_host(cls, context):
        from .install import install_host

        install_host(context)

    @classmethod
    def install(cls, context):
        from .install import install

        install(context)

    @classmethod
    def uninstall(cls, context):
        from .install import uninstall

        uninstall(context)

    @classmethod
    def uninstall_host(cls, context):
        from .install import uninstall_host

        uninstall_host(context)

    @classmethod
    def compose(cls, context):
        from .install import compose

        return compose(context)

    descriptor = ModuleDescriptor(
        name="k3s",
        slot="runtime",
        version="0.4.0",
        side="node",
        parameters=Options.model_json_schema(),
        accepts={
            "volume": {"kubernetes-pvc"},
            "image": {"oci-registry", "node-local"},
            "gpu_allocation": {"nvidia-mps", "ascend-device"},
            "readonly_directory": {"local-directory"},
        },
        provides={"snapshot": {"oci-archive"}, FEATURE: {"ssh"}},
        events={
            "workspace_ssh_connected": LocalizedText(en="Workspace SSH connection opened", zh_hans="容器间 SSH 已连接"),
            "workspace_ssh_rejected": LocalizedText(en="Workspace SSH connection rejected", zh_hans="容器间 SSH 连接被拒绝"),
        },
        resources=[
            ResourceDefinition(
                key="cpu",
                unit="cores",
                label=LocalizedText(en="CPU", zh_hans="CPU"),
                default={"interactive": Decimal(2), "batch": Decimal(8)},
                integral=True,
                minimum=Decimal(1),
                step=Decimal(1),
            ),
            ResourceDefinition(
                key="memory",
                unit="GiB",
                label=LocalizedText(en="Memory", zh_hans="内存"),
                default={"interactive": Decimal(4), "batch": Decimal(16)},
                integral=True,
                minimum=Decimal(1),
                step=Decimal(1),
            ),
            ResourceDefinition(
                key="system_storage", unit="GiB", integral=True,
                label=LocalizedText(en="System volume", zh_hans="系统卷"),
                default=Decimal(5), minimum=Decimal(1), step=Decimal(1),
            ),
        ],
    )

    def __init__(self, context, client=None, run=subprocess.run):
        super().__init__(context)
        self.client = client or Client(context.options.get("namespace", "workloads"))
        self.run = run
        self.root = context.state_directory / "runtime"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def record_path(self, task_id):
        return self.root / (hashlib.sha256(task_id.encode()).hexdigest() + ".json")

    def images(self):
        catalog = list(self.context.options.get("images", []))
        installed = self.context.state_directory / "bootstrap" / "catalog.json"
        if installed.exists():
            catalog.extend(json.loads(installed.read_text()))
        images = [
            Image.model_validate(
                {
                    "owner": "shared",
                    "node_id": self.context.node_id,
                    "architecture": self.context.options.get("architecture", "amd64"),
                    **item,
                }
            )
            for item in catalog
        ]
        for image in images:
            if image.node_id != self.context.node_id or image.credentials:
                raise ValueError("runtime.invalid_image_catalog")
        return images

    def capacity(self):
        resources = {"cpu": ("cpu", 1), "memory": ("memory", 2**30),
                     "system_storage": ("ephemeral-storage", 2**30)}
        total = dict.fromkeys(resources, Decimal(0))
        for node in self.client.list("nodes"):
            allocatable = node.get("status", {}).get("allocatable", {})
            for key, (resource, divisor) in resources.items():
                total[key] += quantity(allocatable.get(resource, 0)) / divisor
        return total

    def assess(self, intent):
        total = self.capacity()
        used = dict.fromkeys(total, Decimal(0))
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text())
            if not record.get("terminal") or record.get("stopping"):
                for key in used:
                    used[key] += Decimal(record["task"]["resources"].get(key, 0))
        defaults = {"cpu": Decimal(1), "memory": Decimal(1), "system_storage": Decimal(5)}
        quantities = {key: intent.resources.get(key, default) for key, default in defaults.items()}
        if any(value < 1 or value != value.to_integral_value() for value in quantities.values()):
            return ResourceAssessment(
                feasible=False, quantities=quantities, reason=Message(code="runtime.invalid_resource_request")
            )
        feasible = all(value <= total.get(key, 0) - used.get(key, 0) for key, value in quantities.items())
        return ResourceAssessment(
            feasible=feasible,
            quantities=quantities,
            reason=None if feasible else Message(code="runtime.capacity_unavailable"),
        )

    def read(self, task_id):
        return json.loads(self.record_path(task_id).read_text())

    def save(self, task_id, record):
        path = self.record_path(task_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        temporary.chmod(0o600)
        temporary.replace(path)

    def start(self, task, inputs):
        if task.node_id != self.context.node_id:
            raise ValueError("runtime.wrong_node")
        if self.record_path(task.id).exists():
            record = self.read(task.id)
            if record["task"] != task.model_dump(mode="json"):
                raise ValueError("runtime.task_conflict")
            return self.status(task.id)
        objects = task_objects(task, inputs, self.context.options, state_directory=self.context.state_directory)
        self.save(
            task.id,
            {
                "task": task.model_dump(mode="json"),
                "objects": [(o["kind"], o["metadata"]["name"]) for o in objects],
                "preparing": objects,
            },
        )
        return self.status(task.id)

    def status(self, task_id):
        record = self.read(task_id)
        if record.get("stopping"):
            return self.stop(task_id)
        if record.get("terminal"):
            return TaskStatus.model_validate(record["terminal"])
        if record.get("preparing") and not record.get("submission_attempted"):
            for obj in record["preparing"]:
                if obj["kind"] in {"Pod", "Job"}:
                    record["submission_attempted"] = True
                    self.save(task_id, record)
                self.client.ensure(obj)
            del record["preparing"]
            self.save(task_id, record)
        task = TaskSpec.model_validate(record["task"])
        pods = self.client.list("pods", "platform.task-key=" + name_for(task_id))
        workload = self.client.get("Job" if task.kind == "batch" else "Pod", name_for(task_id))
        if workload is None:
            status = TaskStatus(
                id=task_id,
                state="failed",
                error=Message(code="runtime.workload_missing"),
                ended_at=datetime.now(timezone.utc),
            )
        else:
            self.client.check_owner(workload, task_id)
            status = TaskStatus(id=task_id, state="queued", usage=task.resources, port=task.login_port)
            for pod in pods:
                self.client.check_owner(pod, task_id)
                current = pod.get("status", {})
                terminated = next(
                    (
                        c.get("state", {}).get("terminated")
                        for c in current.get("containerStatuses", [])
                        if c.get("name") == "workspace"
                    ),
                    None,
                )
                if terminated:
                    code = terminated["exitCode"]
                    status = TaskStatus(
                        id=task_id,
                        state="ended" if code == 0 else "failed",
                        exit_code=code,
                        started_at=terminated.get("startedAt"),
                        ended_at=terminated.get("finishedAt") or datetime.now(timezone.utc),
                    )
                    break
                if current.get("phase") in {"Failed", "Succeeded"}:
                    status = TaskStatus(
                        id=task_id,
                        state="failed",
                        error=Message(code="runtime.exit_unknown"),
                        ended_at=datetime.now(timezone.utc),
                    )
                    break
                if current.get("phase") == "Running":
                    ready = any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in current.get("conditions", [])
                    )
                    status.state = "running" if ready else "starting"
                    status.started_at = current.get("startTime")
                elif not pod.get("spec", {}).get("schedulingGates"):
                    status.state = "starting"
            if status.port is None:
                service = self.client.get("Service", name_for(task_id))
                if service:
                    status.port = service.get("spec", {}).get("ports", [{}])[0].get("nodePort")
        if status.state not in ACTIVE_STATES:
            record["terminal"] = status.model_dump(mode="json")
            self.save(task_id, record)
        return status

    def stop(self, task_id):
        record = self.read(task_id)
        record["stopping"] = True
        record.pop("preparing", None)
        self.save(task_id, record)
        try:
            captured = self.logs(task_id, 10000)
        except (OSError, ValueError, subprocess.SubprocessError):
            captured = ""
        if captured:
            log = self.record_path(task_id).with_suffix(".log")
            temporary = log.with_suffix(".log.tmp")
            temporary.write_text(captured[-1024 * 1024 :])
            temporary.chmod(0o600)
            temporary.replace(log)
        for kind, name in reversed(record["objects"]):
            self.client.delete(kind, name, task_id)
        for pod in self.client.list("pods", "platform.task-key=" + name_for(task_id)):
            self.client.delete("Pod", pod["metadata"]["name"], task_id)
        remaining = self.client.list("pods", "platform.task-key=" + name_for(task_id))
        if any(pod.get("status", {}).get("phase") not in {"Failed", "Succeeded"} for pod in remaining):
            return TaskStatus(id=task_id, state="running", usage=record["task"]["resources"])
        status = (
            TaskStatus.model_validate(record["terminal"])
            if record.get("terminal")
            else TaskStatus(id=task_id, state="ended", ended_at=datetime.now(timezone.utc))
        )
        record["terminal"] = status.model_dump(mode="json")
        record.pop("stopping", None)
        self.save(task_id, record)
        return status

    def workspace_ssh_endpoint(self, task_id: str, owner: str) -> PortConnection | None:
        from ipaddress import ip_address

        record = self.read(task_id)
        task = record["task"]
        if (task["owner"] != owner or FEATURE not in task.get("extensions", {})
                or record.get("terminal") or record.get("stopping")):
            return None
        name = name_for(task_id)
        pods = self.client.list("Pod", f"platform.task-key={name}") if task["kind"] == "batch" else [
            self.client.get("Pod", name)
        ]
        for pod in pods:
            if not pod:
                continue
            self.client.check_owner(pod, task_id)
            status = pod.get("status", {})
            if (pod["metadata"].get("deletionTimestamp") or status.get("phase") != "Running"
                    or not any(item.get("type") == "Ready" and item.get("status") == "True"
                               for item in status.get("conditions", []))):
                continue
            return PortConnection(host=str(ip_address(status["podIP"])), port=SSH_PORT)
        return None

    def list_tasks(self):
        result = []
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text())
            if not record.get("acknowledged"):
                result.append(self.status(record["task"]["id"]))
        return result

    def acknowledge(self, task_id):
        record = self.read(task_id)
        if not record.get("terminal") or record.get("stopping"):
            raise ValueError("runtime.task_cleanup_pending")
        record["acknowledged"] = True
        self.save(task_id, record)

    def resources(self):
        total = self.capacity()
        used = {
            key: sum(s.usage.get(key, 0) for s in self.list_tasks() if s.state in ACTIVE_STATES)
            for key in total
        }
        return ResourceUsage(total=total, used=used)

    def expire(self, now):
        ended = []
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text())
            if record.get("acknowledged"):
                continue
            task = TaskSpec.model_validate(record["task"])
            if not record.get("terminal") and task.expires_at and task.expires_at <= now:
                ended.append(self.stop(task.id))
        return ended

    def extend(self, task_id, expires_at):
        status = self.status(task_id)
        if status.state not in ACTIVE_STATES:
            raise ValueError("runtime.task_ended")
        record = self.read(task_id)
        task = TaskSpec.model_validate(record["task"])
        if task.kind != "interactive" or expires_at <= datetime.now(timezone.utc):
            raise ValueError("runtime.invalid_expiry")
        task.expires_at = expires_at
        self.client.call(
            "annotate",
            "pod",
            name_for(task_id),
            "platform.expires-at=" + expires_at.isoformat(),
            "--overwrite",
            "-o",
            "json",
        )
        record["task"] = task.model_dump(mode="json")
        self.save(task_id, record)
        return status

    def logs(self, task_id, tail=200):
        self.read(task_id)
        pods = self.client.list("pods", "platform.task-key=" + name_for(task_id))
        output = []
        for pod in pods:
            self.client.check_owner(pod, task_id)
            output.append(self.client.logs(pod["metadata"]["name"], tail))
        if output:
            return "\n".join(output)
        log = self.record_path(task_id).with_suffix(".log")
        return "\n".join(log.read_text().splitlines()[-tail:]) if log.exists() else ""

    def set_authorized_keys(self, task_id, keys):
        record = self.read(task_id)
        if self.status(task_id).state not in ACTIVE_STATES:
            raise ValueError("runtime.task_ended")
        for key in keys:
            if "\n" in key or "\r" in key:
                raise ValueError("runtime.invalid_key")
        name = name_for(task_id)
        secret = self.client.get("Secret", name)
        if secret is None:
            raise FileNotFoundError("runtime.secret_missing")
        self.client.check_owner(secret, task_id)
        self.client.call(
            "patch",
            "secret",
            name,
            "--type=merge",
            "-p",
            json.dumps(
                {
                    "metadata": {"resourceVersion": secret["metadata"]["resourceVersion"]},
                    "stringData": {"authorized_keys": "\n".join(keys) + "\n"},
                }
            ),
            "-o",
            "json",
        )
        record["task"]["identity"]["ssh_keys"] = keys
        self.save(task_id, record)

    def snapshot(self, task_id, owner):
        record = self.read(task_id)
        if record["task"]["owner"] != owner:
            raise PermissionError("runtime.owner_mismatch")
        pods = self.client.list("pods", "platform.task-key=" + name_for(task_id))
        if len(pods) != 1 or pods[0].get("status", {}).get("phase") != "Running":
            raise ValueError("runtime.snapshot_requires_running_task")
        pod = pods[0]
        self.client.check_owner(pod, task_id)
        container = next(
            c["containerID"] for c in pod["status"]["containerStatuses"] if c["name"] == "workspace"
        ).removeprefix("containerd://")
        if not re.fullmatch("[a-f0-9]{64}", container):
            raise ValueError("runtime.invalid_container")
        command = [
            self.context.options.get("nerdctl", "nerdctl"),
            "--address",
            "/run/k3s/containerd/containerd.sock",
            "--namespace",
            "k8s.io",
        ]
        differences = self.run(
            [*command, "diff", container], check=True, capture_output=True, text=True, timeout=60
        ).stdout
        mounts = [
            m["mountPath"].rstrip("/") for c in pod["spec"]["containers"] for m in c.get("volumeMounts", [])
        ]
        for line in differences.splitlines():
            path = line[2:].strip()
            if any(path == mount or path.startswith(mount + "/") for mount in mounts):
                continue
            if re.search(
                r"(^|/)(\.ssh|\.aws|\.kube|\.env|credentials|id_rsa|id_ed25519)(/|$)|\.(pem|key)$", path
            ):
                raise ValueError("runtime.snapshot_contains_credentials")
        identifier = uuid4().hex
        reference = "localhost/platform-snapshot:" + identifier
        location = self.root / (identifier + ".oci.tar")
        raw = self.root / (identifier + ".raw.tar")
        try:
            self.run(
                [*command, "commit", "--pause=true", "--format", "oci", container, reference],
                check=True,
                timeout=300,
            )
            self.run(
                ["k3s", "ctr", "--namespace", "k8s.io", "images", "export", str(raw), reference],
                check=True,
                timeout=300,
            )
            raw.chmod(0o600)
            sanitize_snapshot(raw, location)
        except Exception:
            location.unlink(missing_ok=True)
            raise
        finally:
            raw.unlink(missing_ok=True)
            self.run(
                ["k3s", "ctr", "--namespace", "k8s.io", "images", "rm", reference], check=False, timeout=60
            )
        return Snapshot(
            id=identifier,
            owner=owner,
            task_id=task_id,
            node_id=self.context.node_id,
            form="oci-archive",
            location=str(location),
            size_bytes=location.stat().st_size,
        )

    def release_snapshot(self, snapshot_id):
        if not re.fullmatch(r"[a-f0-9]{32}", snapshot_id):
            raise ValueError("runtime.invalid_snapshot")
        (self.root / (snapshot_id + ".oci.tar")).unlink(missing_ok=True)
