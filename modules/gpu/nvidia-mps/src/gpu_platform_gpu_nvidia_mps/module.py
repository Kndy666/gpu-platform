import csv
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from pydantic import Field

from gpu_platform_contracts.base import Contract
from gpu_platform_contracts.handoffs import GPUAllocation
from gpu_platform_contracts.assessment import ResourceAssessment
from gpu_platform_contracts.module import ModuleContext, ModuleDescriptor, SlotPending
from gpu_platform_contracts.resources import GPUDevice, GPURequest, ResourceDefinition, ResourceUsage
from gpu_platform_contracts.slots.gpu import GPU
from gpu_platform_contracts.texts import LocalizedText, Message

from .allocation import choose_devices, resolve_gpu_max
from .hardware import cuda_inventory


class Options(Contract):
    node_container_name: str = Field(default="gpu-platform-node", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        description="Node service container invoked by the host OCI GPU hook.")


class Module(GPU):
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
    def uninstall_host(cls, context):
        from .install import uninstall_host

        uninstall_host(context)

    @classmethod
    def compose(cls, context):
        from .install import compose

        return compose(context)

    descriptor = ModuleDescriptor(
        name="nvidia-mps",
        slot="gpu",
        version="0.4.0",
        side="node",
        parameters=Options.model_json_schema(),
        provides={"gpu_allocation": {"nvidia-mps"}},
        resources=[
            ResourceDefinition(
                key="gpu_compute",
                unit="percent",
                label=LocalizedText(en="GPU compute", zh_hans="显卡算力"),
                default=Decimal(100),
                allocation_group="gpu",
                allocation_field="compute_percent",
                per_allocation=True,
                minimum=Decimal(2),
                maximum=Decimal(100),
                step=Decimal(1),
                integral=True,
            ),
            ResourceDefinition(
                key="gpu_memory",
                unit="GiB",
                label=LocalizedText(en="GPU memory", zh_hans="显存"),
                default=Decimal(8),
                allocation_group="gpu",
                allocation_field="memory_gib",
                per_allocation=True,
                minimum=Decimal(1),
                integral=True,
            ),
        ],
        texts={
            "gpu.mps_share": LocalizedText(
                en="Compute shares reserve streaming multiprocessors in pairs; each card totals 100%.",
                zh_hans="算力按两个流式多处理器一组分配，每张卡合计 100%。",
            )
        },
    )

    def __init__(self, context, inventory=cuda_inventory, run=subprocess.check_output):
        super().__init__(context)
        self.read_inventory, self.run = inventory, run
        self.root = context.state_directory / "gpu"
        self.records = self.root / "leases"
        self.records.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.pipes = self.root / "pipes"
        self.pipes.mkdir(mode=0o711, exist_ok=True)

    @contextmanager
    def locked(self):
        with (self.root / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def key(self, task_id):
        return hashlib.sha256(task_id.encode()).hexdigest()[:32]

    def leases(self):
        return [json.loads(path.read_text()) for path in self.records.glob("*.json")]

    def save(self, record):
        path = self.records / (self.key(record["task"]) + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        temporary.chmod(0o600)
        temporary.replace(path)

    def mps(self, *args):
        return self.run(
            ["nvidia-cuda-mps-control", "-p", "3", *args],
            text=True,
            timeout=30,
            env={
                **os.environ,
                "CUDA_MPS_PIPE_DIRECTORY": str(self.pipes),
                "CUDA_MPS_LOG_DIRECTORY": str(self.root / "logs"),
            },
        )

    def inventory(self):
        leases = self.leases()
        result = []
        for device in self.read_inventory():
            used = [a for record in leases for a in record["allocations"] if a["uuid"] == device["uuid"]]
            result.append(
                GPUDevice(
                    id=device["uuid"],
                    name=device["name"],
                    memory_gib=Decimal(device["usable_memory_mib"]) / 1024,
                    used_compute_percent=sum(a["compute_percent"] for a in used),
                    used_memory_gib=Decimal(sum(a["memory_mib"] for a in used)) / 1024,
                )
            )
        return result

    def selection(self, requests):
        devices = self.read_inventory()
        desired = []
        for request in requests:
            if request.compute_percent != int(request.compute_percent) or request.compute_percent < 2:
                raise ValueError("gpu.invalid_compute_increment")
            if request.memory_gib != int(request.memory_gib) or request.memory_gib < 1:
                raise ValueError("gpu.invalid_memory_increment")
            desired.append(
                {"compute_percent": int(request.compute_percent), "memory_gib": float(request.memory_gib)}
            )
        from itertools import permutations

        leases = [lease["allocations"] for lease in self.leases()]
        allocation = None
        for group in permutations(devices, len(requests)):
            if any(
                not r.device_id.startswith("auto:") and r.device_id != d["uuid"]
                for r, d in zip(requests, group)
            ):
                continue
            selected = []
            for index, (device, request) in enumerate(zip(group, desired)):
                candidate = choose_devices([device], leases, [request])
                if not candidate:
                    break
                candidate[0]["slot"] = index
                selected.extend(candidate)
            else:
                allocation = selected
                break
        return allocation

    def assess(self, intent):
        requests = list(intent.gpus)
        definitions = {
            item.allocation_field: item
            for item in self.descriptor.resources
            if item.allocation_group == "gpu" and item.allocation_field is not None
        }
        generic = intent.allocation_requests.get("gpu", [])
        if generic:
            try:
                rows = []
                for row in generic:
                    if set(row) != {item.key for item in definitions.values()}:
                        raise ValueError("gpu.invalid_request")
                    converted = {}
                    for field, definition in definitions.items():
                        value = row[definition.key]
                        if value != "max" and Decimal(value) != int(value):
                            raise ValueError("gpu.invalid_request")
                        converted[field] = "max" if value == "max" else int(value)
                    rows.append(converted)
                inventory = self.read_inventory()
                compute, memory = definitions["compute_percent"].key, definitions["memory_gib"].key
                resolved = resolve_gpu_max(
                    rows,
                    {
                        "devices": [
                            {
                                "sm_count": item["sm_count"],
                                "memory_gib": int(item["usable_memory_mib"]) // 1024,
                            }
                            for item in inventory
                        ],
                        "compute_per_card_percent": int(intent.per_allocation_limits[compute])
                        if intent.per_allocation_limits.get(compute) is not None
                        else 100,
                        "memory_per_card_gib": int(intent.per_allocation_limits[memory])
                        if intent.per_allocation_limits.get(memory) is not None
                        else max((item["usable_memory_mib"] // 1024 for item in inventory), default=0),
                        "compute_total_percent": intent.limits.get(compute),
                        "memory_total_gib": intent.limits.get(memory),
                    },
                )
                requests = [
                    GPURequest(device_id=f"auto:{index}", **row) for index, row in enumerate(resolved)
                ]
            except (ValueError, TypeError):
                return ResourceAssessment(feasible=False, reason=Message(code="gpu.invalid_request"))
        quantities = {
            "gpu_compute": sum((item.compute_percent for item in requests), Decimal(0)),
            "gpu_memory": sum((item.memory_gib for item in requests), Decimal(0)),
        }
        allocations = {
            "gpu": [
                {definition.key: getattr(request, field) for field, definition in definitions.items()}
                for request in requests
            ]
        }
        try:
            with self.locked():
                allocation = self.selection(requests)
        except ValueError:
            return ResourceAssessment(
                feasible=False, quantities=quantities, reason=Message(code="gpu.invalid_request")
            )
        if allocation is None:
            return ResourceAssessment(
                feasible=False,
                quantities=quantities,
                gpus=requests,
                allocations=allocations,
                reason=Message(code="gpu.capacity_unavailable"),
            )
        return ResourceAssessment(
            feasible=True,
            quantities=quantities,
            allocations=allocations,
            gpus=[
                GPURequest(
                    device_id=item["uuid"],
                    compute_percent=item["compute_percent"],
                    memory_gib=Decimal(str(item["memory_mib"])) / 1024,
                )
                for item in allocation
            ],
        )

    def allocate(self, task_id, requests):
        with self.locked():
            self.ensure_server()
            path = self.records / (self.key(task_id) + ".json")
            if path.exists():
                record = json.loads(path.read_text())
                if record["request"] != [r.model_dump(mode="json") for r in requests]:
                    raise ValueError("gpu.allocation_conflict")
                if record.get("prepared"):
                    return GPUAllocation.model_validate(record["handoff"])
                self._release(record)
            allocation = self.selection(requests)
            if allocation is None:
                raise SlotPending(reason="gpu.capacity_unavailable")
            identifier = self.key(task_id)
            token = secrets.token_urlsafe(32)
            for index, entry in enumerate(allocation):
                entry["partition"] = f"p_{identifier}_{index}"
            handoff = GPUAllocation(
                id=identifier,
                task_id=task_id,
                node_id=self.context.node_id,
                form="nvidia-mps",
                devices=[
                    GPURequest(
                        device_id=a["uuid"],
                        compute_percent=a["compute_percent"],
                        memory_gib=Decimal(str(a["memory_mib"])) / 1024,
                    )
                    for a in allocation
                ],
                environment={
                    "NVIDIA_VISIBLE_DEVICES": ",".join(a["uuid"] for a in allocation),
                    "CUDA_VISIBLE_DEVICES": ",".join(a["uuid"] for a in allocation),
                    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
                    "CUDA_MPS_PIPE_DIRECTORY": "/run/platform-mps",
                    "PLATFORM_GPU_TASK": task_id,
                    "PLATFORM_GPU_LEASE": token,
                    "PLATFORM_GPU_DEVICES": ",".join(a["uuid"] for a in allocation),
                },
                mounts=[
                    {
                        "source": str(self.pipes / "shared" / ("t_" + identifier)),
                        "target": "/run/platform-mps",
                        "read_only": True,
                    }
                ],
            )
            record = {
                "task": task_id,
                "token": token,
                "request": [r.model_dump(mode="json") for r in requests],
                "allocations": allocation,
                "prepared": False,
                "handoff": handoff.model_dump(mode="json"),
            }
            self.save(record)
            try:
                for entry in allocation:
                    self.mps(
                        "sm-partition",
                        "create",
                        entry["partition"],
                        "--server=shared",
                        "--device=" + entry["uuid"],
                        "--chunks=" + str(entry["sm_count"] // 2),
                    )
                self.mps("namespace", "create", "t_" + identifier, "--server=shared")
                for entry in allocation:
                    self.mps(
                        "namespace",
                        "set",
                        "t_" + identifier,
                        "--server=shared",
                        "--device=" + entry["uuid"],
                        "--sm-partition=" + entry["partition"],
                    )
                record["prepared"] = True
                self.save(record)
            except Exception:
                self._release(record)
                raise
            return handoff

    def lookup(self, task_id):
        with self.locked():
            path = self.records / (self.key(task_id) + ".json")
            if not path.exists():
                return None
            return GPUAllocation.model_validate(json.loads(path.read_text())["handoff"])

    def ensure_server(self):
        devices = self.read_inventory()
        (self.root / "logs").mkdir(mode=0o700, exist_ok=True)
        try:
            servers = self.mps("server", "list", "--format=csv,noheader")
        except subprocess.SubprocessError:
            if self.leases():
                raise RuntimeError("gpu.server_lost")
            ownership = self.root / "server-ownership.json"
            record = json.loads(ownership.read_text()) if ownership.exists() else {"compute_modes": {}}
            for device in devices:
                if device["uuid"] not in record["compute_modes"]:
                    mode = self.run(["nvidia-smi", "-i", device["uuid"], "--query-gpu=compute_mode",
                                     "--format=csv,noheader"], text=True, timeout=30).strip()
                    modes = {"default": 0, "exclusive_thread": 1, "prohibited": 2, "exclusive_process": 3}
                    record["compute_modes"][device["uuid"]] = modes[mode.lower().replace(" ", "_")]
            record["daemon"] = True
            temporary = ownership.with_suffix(".tmp")
            temporary.write_text(json.dumps(record))
            temporary.chmod(0o600)
            temporary.replace(ownership)
            for device in devices:
                self.run(
                    ["nvidia-smi", "-i", device["uuid"], "-c", "EXCLUSIVE_PROCESS"], text=True, timeout=30
                )
            self.run(
                ["nvidia-cuda-mps-control", "-d", "-p", "3", "-m"],
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    "CUDA_MPS_PIPE_DIRECTORY": str(self.pipes),
                    "CUDA_MPS_LOG_DIRECTORY": str(self.root / "logs"),
                },
            )
            self.mps("context-share", "set", "default_socket=off")
            servers = ""
        if not any(
            row and row[0].strip() == "shared" and any(value.strip() == "Ready" for value in row)
            for row in csv.reader(servers.splitlines())
        ):
            if self.leases():
                raise RuntimeError("gpu.server_lost")
            ownership = self.root / "server-ownership.json"
            record = json.loads(ownership.read_text()) if ownership.exists() else {}
            record["server"] = True
            temporary = ownership.with_suffix(".tmp")
            temporary.write_text(json.dumps(record))
            temporary.chmod(0o600)
            temporary.replace(ownership)
            self.mps(
                "server",
                "create",
                "shared",
                "--uid=0",
                "--allowed-devices=" + ",".join(d["uuid"] for d in devices),
            )

    def _release(self, record):
        identifier = self.key(record["task"])
        namespaces = list(
            csv.reader(self.mps("namespace", "list", "--server=shared", "--format=csv,noheader").splitlines())
        )
        if any("t_" + identifier in [v.strip() for v in row] for row in namespaces):
            self.mps("namespace", "delete", "t_" + identifier, "--server=shared", "--force")
        cgroup = record.get("cgroup")
        if cgroup and Path(cgroup).exists():
            for entry in record["allocations"]:
                self.run(
                    [
                        "nvidia-smi",
                        "memory-limits",
                        "-i",
                        entry["uuid"],
                        "-n",
                        cgroup,
                        "--soft-limit",
                        "0",
                        "--hard-limit",
                        "max",
                    ],
                    text=True,
                    timeout=30,
                )
        partitions = list(
            csv.reader(
                self.mps(
                    "sm-partition", "list", "--server=shared", "--all", "--format=csv,noheader"
                ).splitlines()
            )
        )
        for entry in record["allocations"]:
            if any(entry["partition"] in [v.strip() for v in row] for row in partitions):
                self.mps(
                    "sm-partition",
                    "delete",
                    entry["partition"],
                    "--server=shared",
                    "--device=" + entry["uuid"],
                )
        (self.records / (identifier + ".json")).unlink(missing_ok=True)

    def release(self, allocation_id):
        if not all(c in "0123456789abcdef" for c in allocation_id) or len(allocation_id) != 32:
            raise ValueError("gpu.invalid_allocation")
        with self.locked():
            path = self.records / (allocation_id + ".json")
            if path.exists():
                self._release(json.loads(path.read_text()))

    def resources(self):
        inventory = self.inventory()
        return ResourceUsage(
            total={
                "gpu_compute": sum(d.compute_percent for d in inventory),
                "gpu_memory": sum(d.memory_gib for d in inventory),
            },
            used={
                "gpu_compute": sum(d.used_compute_percent for d in inventory),
                "gpu_memory": sum(d.used_memory_gib for d in inventory),
            },
        )

    def explanation(self):
        return Message(code="gpu.mps_share")

    def quota_hook(self, state):
        spec = json.loads((Path(state["bundle"]) / "config.json").read_text())
        environment = dict(v.split("=", 1) for v in spec.get("process", {}).get("env", []) if "=" in v)
        task = environment.get("PLATFORM_GPU_TASK")
        if not task:
            return
        with self.locked():
            record = json.loads((self.records / (self.key(task) + ".json")).read_text())
            if not secrets.compare_digest(record["token"], environment.get("PLATFORM_GPU_LEASE", "")):
                raise PermissionError("gpu.lease_mismatch")
            if environment.get("PLATFORM_GPU_DEVICES") != ",".join(a["uuid"] for a in record["allocations"]):
                raise PermissionError("gpu.devices_mismatch")
            cgroup = Path("/proc", str(int(state["pid"])), "cgroup").read_text().strip().split("0::")[1]
            if not cgroup.startswith("/kubepods.slice/") or ".." in Path(cgroup).parts:
                raise PermissionError("gpu.cgroup_mismatch")
            cgroup = "/sys/fs/cgroup" + cgroup
            for entry in record["allocations"]:
                self.run(
                    [
                        "nvidia-smi",
                        "memory-limits",
                        "-i",
                        entry["uuid"],
                        "-n",
                        cgroup,
                        "--soft-limit",
                        str(int(entry["memory_mib"])),
                        "--hard-limit",
                        str(int(entry["memory_mib"])),
                    ],
                    text=True,
                    timeout=30,
                )
            record["cgroup"] = cgroup
            self.save(record)


if __name__ == "__main__":
    if sys.argv[1:] != ["hook"]:
        raise SystemExit("Expected hook")
    Module(
        ModuleContext(
            state_directory=Path(os.environ.get("PLATFORM_STATE_DIRECTORY", "/var/lib/gpu-platform"))
        )
    ).quota_hook(json.load(sys.stdin))
