"""Lease entire physical Ascend chips through the GPU slot contract."""

import fcntl
import hashlib
import json
from contextlib import contextmanager
from decimal import Decimal

from gpu_platform_contracts.assessment import ResourceAssessment
from gpu_platform_contracts.handoffs import GPUAllocation
from gpu_platform_contracts.module import ModuleDescriptor, SlotError
from gpu_platform_contracts.resources import GPUDevice, GPURequest, ResourceDefinition, ResourceQuotaRule, ResourceUsage
from gpu_platform_contracts.slots.gpu import GPU
from gpu_platform_contracts.texts import LocalizedText, Message
from .hardware import inventory


class Module(GPU):
    descriptor = ModuleDescriptor(
        name="ascend",
        slot="gpu",
        version="0.4.0",
        side="node",
        provides={"gpu_allocation": {"ascend-device"}},
        resources=[
            ResourceDefinition(
                key="npu_count",
                unit="count",
                integral=True,
                minimum=Decimal(0),
                default={"interactive": Decimal(0), "batch": Decimal(1)},
                label=LocalizedText(en="NPU count", zh_hans="NPU 数量"),
            )
        ],
        quota_rules={
            "npu_count": ResourceQuotaRule(
                source=ResourceDefinition(
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
                basis="per_allocation",
                divisor=Decimal(100),
            )
        },
        texts={
            "gpu.device_label": LocalizedText(en="NPU", zh_hans="NPU"),
            "gpu.memory_label": LocalizedText(en="NPU memory", zh_hans="NPU 内存"),
            "gpu.details_label": LocalizedText(en="NPU allocations", zh_hans="NPU 明细"),
            "ascend.exclusive": LocalizedText(
                en="Each requested NPU is assigned exclusively to one environment.",
                zh_hans="每颗 NPU 由一个环境独占。",
            ),
            "ascend.capacity_unavailable": LocalizedText(
                en="The requested NPUs are currently unavailable.", zh_hans="当前没有足够的空闲 NPU。"
            ),
            "ascend.whole_device_required": LocalizedText(
                en="Request whole NPUs with their full memory capacity.",
                zh_hans="请申请完整 NPU 及其全部设备内存。",
            ),
            "ascend.lease_conflict": LocalizedText(
                en="The task already has a different NPU allocation.", zh_hans="任务已有不同的 NPU 分配。"
            ),
            "ascend.driver_required": LocalizedText(
                en="Ascend 310P drivers on ARM64 are required.", zh_hans="需要 ARM64 主机及昇腾 310P 驱动。"
            ),
            "ascend.inventory_unavailable": LocalizedText(
                en="Cannot read the NPU inventory.", zh_hans="无法读取 NPU 设备信息。"
            ),
        },
    )

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

    def __init__(self, context, read_inventory=inventory):
        super().__init__(context)
        self.read_inventory = read_inventory
        self.root = context.state_directory / "ascend"
        self.records = self.root / "leases"
        self.records.mkdir(mode=0o700, parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        with (self.root / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def path(self, task):
        return self.records / (hashlib.sha256(task.encode()).hexdigest() + ".json")

    def leases(self):
        records = []
        for path in self.records.glob("*.json"):
            try:
                records.append(json.loads(path.read_text()))
            except FileNotFoundError:
                continue
        return records

    def occupied(self):
        return {device for lease in self.leases() for device in lease["device_ids"]}

    def inventory(self):
        occupied = self.occupied()
        return [
            GPUDevice(
                id=d["id"],
                name=d["name"],
                memory_gib=d["memory_gib"],
                compute_percent=Decimal(100) if d["ready"] else Decimal(0),
                used_compute_percent=Decimal(100) if d["id"] in occupied else Decimal(0),
                used_memory_gib=d["memory_gib"] if d["id"] in occupied else Decimal(0),
            )
            for d in self.read_inventory()
        ]

    def selection(self, requests):
        available = {
            d["id"]: d for d in self.read_inventory() if d["ready"] and d["id"] not in self.occupied()
        }
        selected = []
        for request in requests:
            device = (
                next((d for d in available.values() if d["memory_gib"] == request.memory_gib), None)
                if request.device_id.startswith("auto:")
                else available.get(request.device_id)
            )
            if device is None:
                raise SlotError("ascend.capacity_unavailable")
            if request.compute_percent != 100 or request.memory_gib != device["memory_gib"]:
                raise SlotError("ascend.whole_device_required")
            selected.append(device)
            del available[device["id"]]
        return selected

    def assess(self, intent):
        count = intent.resources.get("npu_count", len(intent.gpus))
        if count != int(count) or count < 0:
            return ResourceAssessment(feasible=False, reason=Message(code="ascend.whole_device_required"))
        count = int(count)
        requests = list(intent.gpus)
        available = [d for d in self.read_inventory() if d["ready"] and d["id"] not in self.occupied()]
        if not requests:
            requests = [
                GPURequest(device_id=d["id"], compute_percent=Decimal(100), memory_gib=d["memory_gib"])
                for d in available[:count]
            ]
        try:
            if len(requests) != count:
                raise SlotError("ascend.capacity_unavailable")
            self.selection(requests)
        except SlotError as error:
            return ResourceAssessment(
                feasible=False, quantities={"npu_count": Decimal(count)}, reason=error.message
            )
        return ResourceAssessment(feasible=True, quantities={"npu_count": Decimal(count)}, gpus=requests)

    def lookup(self, task_id):
        path = self.path(task_id)
        return (
            GPUAllocation.model_validate(json.loads(path.read_text())["handoff"]) if path.exists() else None
        )

    def allocate(self, task_id, requests):
        with self.locked():
            path = self.path(task_id)
            serialized = [r.model_dump(mode="json") for r in requests]
            if path.exists():
                record = json.loads(path.read_text())
                if record["requests"] != serialized:
                    raise SlotError("ascend.lease_conflict")
                return GPUAllocation.model_validate(record["handoff"])
            devices = self.selection(requests)
            handoff = GPUAllocation(
                form="ascend-device",
                node_id=self.context.node_id,
                id=task_id,
                task_id=task_id,
                devices=[
                    GPURequest(device_id=d["id"], compute_percent=Decimal(100), memory_gib=d["memory_gib"])
                    for d in devices
                ],
                environment={"ASCEND_VISIBLE_DEVICES": ",".join(str(d["index"]) for d in devices)},
            )
            record = {
                "requests": serialized,
                "device_ids": [d["id"] for d in devices],
                "handoff": handoff.model_dump(mode="json"),
            }
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record))
            temporary.chmod(0o600)
            temporary.replace(path)
            return handoff

    def release(self, allocation_id):
        with self.locked():
            self.path(allocation_id).unlink(missing_ok=True)

    def resources(self):
        devices = self.inventory()
        return ResourceUsage(
            total={"npu_count": sum(d.compute_percent > 0 for d in devices)},
            used={"npu_count": sum(d.used_compute_percent > 0 for d in devices)},
        )

    def explanation(self):
        return Message(code="ascend.exclusive")
