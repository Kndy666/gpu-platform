"""Validate intents, assemble handoffs, and invoke the selected slots."""

import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from gpu_platform_contracts.handoffs import TaskInputs, VolumeRequest
from gpu_platform_contracts.assessment import ResourceAssessment, ResourceIntent
from gpu_platform_contracts.messages import (
    ACTIVE_STATES,
    CapabilityReport,
    Command,
    NodeReport,
    Operation,
    Reply,
    TaskSpec,
    TaskStatus,
    compatible_version,
)
from gpu_platform_contracts.module import SlotError, SlotPending
from gpu_platform_contracts.resources import Quantity, ResourceUsage
from gpu_platform_contracts.slots.build import BuildResult, BuildSpec
from gpu_platform_contracts.texts import Message

from ..autonomy import stop_expired
from ..datasets import Datasets
from ..events import Events
from ..operations import Operations
from ..state import read_json, write_json
from ..sync import Streams

logger = logging.getLogger(__name__)


class NodeEngine:
    def __init__(
        self,
        node_id: str,
        architecture: Literal["amd64", "arm64"],
        slots: dict,
        state_directory: Path,
        features=None,
        datasets: Datasets | None = None,
    ):
        self.node_id = node_id
        self.architecture = architecture
        self.slots = slots
        self.directory = state_directory
        self.features = features or {}
        self.datasets = datasets
        self.streams = Streams(state_directory / "streams", receiver=self.receive_dataset_file)
        self.lock = threading.RLock()
        self.state_path = state_directory / "node-state.json"
        self.state = read_json(self.state_path, {"maintenance": False, "tasks": {}, "allocations": {}})
        self.state.setdefault("builds", {})
        self.state.setdefault("build_results", {})
        self.state.setdefault("terminal_tasks", {})
        self.operations = Operations(
            state_directory / "operations", self.streams.exists
        )
        self.events = Events(state_directory / "events")
        for slot in self.slots.values():
            if hasattr(slot, "context"):
                slot.context.bind_events(self.events.publish)

    def save(self):
        with self.lock:
            write_json(self.state_path, self.state)

    def slot(self, name):
        if name not in self.slots:
            raise SlotError("slot.unavailable", slot=name)
        return self.slots[name]

    def require_feature(self, name):
        if not self.features.get(name, False):
            raise SlotError("feature.disabled", feature=name)

    def dataset_service(self) -> Datasets:
        if self.datasets is None:
            raise SlotError("datasets.unavailable")
        return self.datasets

    def receive_dataset_file(self, payload, chunks):
        self.require_feature("dataset_sync")
        self.dataset_service().receive_file(payload["session_id"], payload["path"], payload["offset"], chunks)

    def usage(self):
        total: dict[str, Decimal] = {}
        used: dict[str, Decimal] = {}
        pools = []
        for name in ("runtime", "storage", "registry", "gpu"):
            if name in self.slots:
                usage = self.slots[name].resources()
                pools.extend(usage.pools)
                for key, value in usage.total.items():
                    total[key] = total.get(key, 0) + value
                for key, value in usage.used.items():
                    used[key] = used.get(key, 0) + value
        return ResourceUsage(total=total, used=used, pools=pools)

    def capabilities(self):
        return CapabilityReport(
            node_id=self.node_id,
            architecture=self.architecture,
            modules=[slot.descriptor for slot in self.slots.values()],
            features=self.features,
            resources=self.usage(),
            gpus=self.slots["gpu"].inventory() if "gpu" in self.slots else [],
            gpu_explanation=self.slots["gpu"].explanation() if "gpu" in self.slots else None,
            images=[image.model_copy(update={"credentials": {}}) for image in self.slot("runtime").images()],
        )

    def report(self):
        tasks = {task.id: task for task in self.slot("runtime").list_tasks()}
        tasks.update({
            task_id: TaskStatus.model_validate(status)
            for task_id, status in self.state["terminal_tasks"].items()
            if task_id in self.state["tasks"]
        })
        return NodeReport(
            node_id=self.node_id,
            observed_at=datetime.now(timezone.utc),
            maintenance=self.state["maintenance"],
            tasks=list(tasks.values()),
            usage=self.usage(),
            metrics=self.slots["metrics"].sample() if "metrics" in self.slots else {},
            events=self.events.pending(),
            sync=[*self.operations.reports(), *self.streams.reports()],
        )

    def release(self, task_id):
        allocation = self.state["allocations"].get(task_id)
        if allocation is None and self.state["tasks"].get(task_id, {}).get("gpus"):
            recovered = self.slot("gpu").lookup(task_id)
            if recovered is not None:
                allocation = recovered.id
                self.state["allocations"][task_id] = allocation
                self.save()
        if allocation is not None:
            self.slot("gpu").release(allocation)
            del self.state["allocations"][task_id]
            self.save()

    def status(self, task_id):
        if task_id in self.state["terminal_tasks"]:
            return TaskStatus.model_validate(self.state["terminal_tasks"][task_id])
        return self.slot("runtime").status(task_id)

    def stop(self, task_id):
        runtime = self.slot("runtime")
        try:
            runtime.status(task_id)
        except (KeyError, FileNotFoundError):
            if task_id not in self.state["tasks"] and task_id not in self.state["terminal_tasks"]:
                raise
            # Preparation can fail before a runtime record exists. Keep a durable result
            # so cancellation, expiry, and acknowledgement can all be retried.
            if task_id not in self.state["terminal_tasks"]:
                self.state["terminal_tasks"][task_id] = TaskStatus(
                    id=task_id, state="ended", ended_at=datetime.now(timezone.utc)
                ).model_dump(mode="json")
                self.save()
            status = self.status(task_id)
        else:
            status = runtime.stop(task_id)
        if status.state not in ACTIVE_STATES:
            self.release(task_id)
        return status

    def start(self, task: TaskSpec):
        if task.node_id != self.node_id:
            raise SlotError("task.wrong_node", node_id=self.node_id)
        recorded = self.state["tasks"].get(task.id)
        if recorded is not None:
            if recorded != task.model_dump(mode="json"):
                raise SlotError("task.conflict", task_id=task.id)
            try:
                return self.status(task.id)
            except (KeyError, FileNotFoundError):
                pass
        if task.id in self.state["terminal_tasks"]:
            return self.status(task.id)
        if self.state["maintenance"]:
            raise SlotError("node.maintenance")
        if task.expires_at is not None and task.expires_at <= datetime.now(timezone.utc):
            raise SlotError("task.expired", task_id=task.id)
        self.state["tasks"][task.id] = task.model_dump(mode="json")
        self.save()
        inputs = TaskInputs()
        for volume in task.volumes:
            inputs.volumes.append(self.slot("storage").create(task.owner, volume))
        catalog = self.slot("runtime").images()
        inputs.image = next((image for image in catalog if task.image in {image.id, image.reference}), None)
        if inputs.image is None and "registry" in self.slots:
            inputs.image = self.slot("registry").get(task.image, task.image_owner or task.owner)
        if inputs.image is None:
            raise SlotError("image.unavailable", image_id=task.image)
        if task.public_datasets:
            self.require_feature("public_datasets")
            if self.datasets is None:
                raise SlotError("datasets.unavailable")
            inputs.datasets = [self.datasets.handoff(value) for value in task.public_datasets]
        if task.gpus:
            inputs.gpu = self.slot("gpu").allocate(task.id, task.gpus)
            self.state["allocations"][task.id] = inputs.gpu.id
            self.save()
        try:
            result = self.slot("runtime").start(task, inputs)
        except Exception:
            # Persist the intent when a backend may have started work before losing its reply.
            try:
                current = self.slot("runtime").status(task.id)
            except (KeyError, FileNotFoundError):
                self.release(task.id)
            else:
                self.state["tasks"][task.id] = task.model_dump(mode="json")
                self.save()
                if current.state not in ACTIVE_STATES:
                    self.release(task.id)
            raise
        self.state["tasks"][task.id] = task.model_dump(mode="json")
        self.save()
        if result.state not in ACTIVE_STATES:
            self.release(task.id)
        return result

    def tick(self):
        with self.lock:
            self.streams.clean_expired()
            self.operations.clean_expired()
            runtime = self.slot("runtime")
            specs = [TaskSpec.model_validate(value) for value in self.state["tasks"].values()]
            for spec in specs:
                if not spec.gpus or "gpu" not in self.slots:
                    continue
                if spec.id not in self.state["allocations"]:
                    recovered = self.slot("gpu").lookup(spec.id)
                    if recovered is None:
                        continue
                    self.state["allocations"][spec.id] = recovered.id
                    self.save()
                try:
                    runtime.status(spec.id)
                except (KeyError, FileNotFoundError):
                    self.release(spec.id)
            stopped = stop_expired(self.stop, specs, datetime.now(timezone.utc))
            # Runtime expiry also covers its own durable records after an interrupted dispatch.
            stopped.extend(runtime.expire(datetime.now(timezone.utc)))
            for status in [*stopped, *runtime.list_tasks()]:
                if status.state not in ACTIVE_STATES:
                    self.stop(status.id)
            for build_id in list(self.state["builds"]):
                try:
                    result = self.slot("build").status(build_id)
                except (KeyError, FileNotFoundError):
                    self.release_build_access(build_id)
                else:
                    if result.state in {"completed", "failed"}:
                        if result.state == "failed":
                            self.slot("build").release_result(build_id)
                        self.release_build_access(build_id)

    def release_build_access(self, build_id):
        if "registry" in self.slots:
            self.slot("registry").release_build_access(build_id)
        with self.lock:
            self.state["builds"].pop(build_id, None)
            self.save()

    def assess(self, intent):
        result = ResourceAssessment(feasible=True, quantities=dict(intent.resources), gpus=intent.gpus)
        if self.state["maintenance"]:
            return ResourceAssessment(feasible=False, reason=Message(code="node.maintenance"))
        for slot in ("runtime", "storage", "gpu"):
            if slot == "gpu" and slot not in self.slots:
                if intent.gpus or intent.allocation_requests:
                    self.slot(slot)
                continue
            assessed = self.slot(slot).assess(intent)
            result.quantities.update(assessed.quantities)
            result.allocations.update(assessed.allocations)
            if slot == "gpu":
                result.gpus = assessed.gpus
            if not assessed.feasible:
                result.feasible = False
                result.reason = assessed.reason
        return result

    def handle(self, command: Command) -> Reply:
        bulk = {
            Operation.IMAGE_EXPORT,
            Operation.IMAGE_IMPORT,
            Operation.IMAGE_SAVE,
            Operation.IMAGE_BUILD,
            Operation.DATASETS_IMPORT,
            Operation.DATASETS_DOWNLOAD,
            Operation.DATASETS_MANIFEST,
        }
        if command.operation in bulk and compatible_version(command.version):
            return self.operations.request(command, self.execute_reply,
                cache_result=command.operation != Operation.DATASETS_IMPORT)
        with self.lock:
            return self.execute_reply(command)

    def execute_reply(self, command: Command) -> Reply:
        try:
            if not compatible_version(command.version):
                raise SlotError("protocol.incompatible", version=command.version)
            result = self.execute(command.operation, command.payload)
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json")
            return Reply(id=command.id, ok=True, data=result or {})
        except SlotError as exc:
            return Reply(id=command.id, ok=False, error=exc.message)
        except BlockingIOError:
            return Reply(id=command.id, ok=False, error=Message(code="operation.pending"))
        except (ValueError, ValidationError, KeyError, FileNotFoundError) as exc:
            logger.info("Command %s rejected: %s", command.operation, type(exc).__name__)
            return Reply(
                id=command.id,
                ok=False,
                error=Message(code="command.invalid", parameters={"operation": command.operation.value}),
            )
        except Exception:
            logger.exception("Command %s failed", command.operation)
            return Reply(
                id=command.id,
                ok=False,
                error=Message(code="command.failed", parameters={"operation": command.operation.value}),
            )

    def execute(self, operation, payload):
        if operation == Operation.EVENTS_ACK:
            self.events.acknowledge(TypeAdapter(list[str]).validate_python(payload["ids"]))
            return {}
        if operation == Operation.RESOURCE_ASSESS:
            return self.assess(ResourceIntent.model_validate(payload))
        if operation == Operation.CAPABILITIES:
            return self.capabilities()
        if operation == Operation.STATUS:
            return self.report()
        if operation == Operation.MAINTENANCE:
            value = payload["enabled"]
            if not isinstance(value, bool):
                raise ValueError("maintenance requires a boolean")
            self.state["maintenance"] = value
            self.save()
            return {"maintenance": value}
        if operation == Operation.TASK_START:
            return self.start(TaskSpec.model_validate(payload))
        if operation == Operation.TASK_STOP:
            return self.stop(payload["task_id"])
        if operation == Operation.TASK_ACK:
            for task_id in TypeAdapter(list[str]).validate_python(payload["ids"]):
                status = self.status(task_id)
                if status.state in ACTIVE_STATES:
                    raise SlotPending(reason="task.cleanup_pending", task_id=task_id)
                status = self.stop(task_id)
                if status.state in ACTIVE_STATES:
                    raise SlotPending(reason="task.cleanup_pending", task_id=task_id)
                if task_id not in self.state["terminal_tasks"]:
                    self.slot("runtime").acknowledge(task_id)
                self.state["tasks"].pop(task_id, None)
                self.save()
            return {}
        if operation == Operation.TASK_STATUS:
            return self.status(payload["task_id"])
        if operation == Operation.TASK_EXTEND:
            expires_at = TypeAdapter(AwareDatetime).validate_python(payload["expires_at"])
            result = self.slot("runtime").extend(payload["task_id"], expires_at)
            if payload["task_id"] in self.state["tasks"]:
                self.state["tasks"][payload["task_id"]]["expires_at"] = expires_at.isoformat()
                self.save()
            return result
        if operation == Operation.TASK_LOGS:
            tail = payload.get("tail", 200)
            if not isinstance(tail, int) or not 1 <= tail <= 10000:
                raise ValueError("invalid log limit")
            return {"logs": self.slot("runtime").logs(payload["task_id"], tail)}
        if operation == Operation.TASK_KEYS:
            keys = TypeAdapter(list[str]).validate_python(payload["keys"])
            self.slot("runtime").set_authorized_keys(payload["task_id"], keys)
            if payload["task_id"] in self.state["tasks"]:
                self.state["tasks"][payload["task_id"]]["identity"]["ssh_keys"] = keys
                self.save()
            return {}
        if operation == Operation.STORAGE_PREPARE:
            return self.slot("storage").create(
                payload["owner"], VolumeRequest.model_validate(payload["request"])
            )
        if operation == Operation.STORAGE_RESIZE:
            size_gib = TypeAdapter(Quantity).validate_python(payload["size_gib"])
            return self.slot("storage").resize(payload["volume_id"], payload["owner"], size_gib)
        if operation == Operation.STORAGE_DELETE:
            self.slot("storage").delete(payload["volume_id"], payload["owner"])
            return {}
        if operation == Operation.STORAGE_COPY:
            return self.slot("storage").copy(
                payload["volume_id"], payload["owner"], VolumeRequest.model_validate(payload["destination"])
            )
        if operation == Operation.IMAGE_SAVE:
            self.require_feature("save_environment")
            snapshot = self.slot("runtime").snapshot(payload["task_id"], payload["owner"])
            try:
                return (
                    self.slot("registry")
                    .store(snapshot, payload["owner"], payload["image_id"])
                    .model_copy(update={"credentials": {}})
                )
            finally:
                self.slot("runtime").release_snapshot(snapshot.id)
        if operation == Operation.IMAGE_BUILD:
            spec = BuildSpec.model_validate(payload)
            original = spec.model_dump(mode="json")
            previous = self.state["build_results"].get(spec.id)
            if previous:
                if previous["spec"] != original:
                    raise SlotError("build.conflict", build_id=spec.id)
                self.slot("build").release_result(spec.id)
                return BuildResult.model_validate(previous["result"])
            if any(image.node_id != self.node_id for image in spec.images):
                raise SlotError("image.wrong_node")
            with self.lock:
                self.state["builds"][spec.id] = {"owner": spec.owner}
                self.save()
            if spec.images:
                spec.images = self.slot("registry").build_images(spec.owner, spec.images, spec.id)
            result = self.slot("build").build(spec)
            if result.state in {"completed", "failed"}:
                self.release_build_access(spec.id)
                if result.state == "completed" and result.image:
                    result.image = (
                        self.slot("registry")
                        .store(result.image, spec.owner, spec.id)
                        .model_copy(update={"credentials": {}})
                    )
                with self.lock:
                    self.state["build_results"][spec.id] = {
                        "spec": original,
                        "result": result.model_dump(mode="json"),
                    }
                    self.save()
                self.slot("build").release_result(spec.id)
            return result
        if operation == Operation.BUILD_CANCEL:
            self.slot("build").cancel(payload["build_id"])
            self.release_build_access(payload["build_id"])
            return {}
        if operation == Operation.IMAGE_DELETE:
            self.slot("registry").delete(payload["image_id"], payload["owner"])
            return {}
        if operation == Operation.IMAGE_EXPORT:
            self.require_feature("image_sync")
            stream_id = uuid4().hex
            size = self.streams.receive(
                stream_id, self.slot("registry").export_image(payload["image_id"], payload["owner"])
            )
            return {"stream_id": stream_id, "size_bytes": size}
        if operation == Operation.IMAGE_IMPORT:
            self.require_feature("image_sync")
            if payload["architecture"] != self.architecture:
                raise SlotError("image.architecture_mismatch")
            result = self.slot("registry").import_image(
                payload["image_id"],
                payload["owner"],
                payload["architecture"],
                self.streams.send(payload["stream_id"]),
            )
            self.streams.remove(payload["stream_id"])
            return result.model_copy(update={"credentials": {}})
        if operation == Operation.DATASETS_LIST:
            self.require_feature("public_datasets")
            return {"entries": self.dataset_service().list(payload.get("path", ""))}
        if operation == Operation.DATASETS_MANIFEST:
            self.require_feature("dataset_sync")
            stream_id = uuid4().hex
            manifest = self.dataset_service().manifest(payload.get("paths", []))
            size = self.streams.receive(stream_id, [self.dataset_service().encode_manifest(manifest)])
            return {"stream_id": stream_id, "size_bytes": size}
        if operation == Operation.DATASETS_DOWNLOAD:
            self.require_feature("public_datasets")
            path = self.dataset_service().resolve(payload["path"])
            stream_id = uuid4().hex

            size = self.streams.file_source(stream_id, path)
            return {"stream_id": stream_id, "size_bytes": size, "filename": path.name}
        if operation == Operation.DATASETS_EXPORT:
            self.require_feature("dataset_sync")
            stream_id = uuid4().hex
            path = self.dataset_service().resolve(payload["path"])
            details = path.stat()
            if not path.is_file() or details.st_size != payload["size_bytes"] or details.st_mtime != payload["modified"]:
                raise ValueError("dataset source changed")
            size = self.streams.file_source(stream_id, path, payload.get("offset", 0))
            return {"stream_id": stream_id, "size_bytes": size}
        if operation == Operation.DATASETS_IMPORT:
            self.require_feature("dataset_sync")
            service, identifier = self.dataset_service(), payload["session_id"]
            action = payload["action"]
            if action == "prepare":
                with self.streams.path(payload["stream_id"]).open("rb") as source:
                    return service.begin(identifier, source, payload.get("paths", []))
            if action == "file":
                result = service.prepare_file(identifier, payload["path"])
                if result["complete"]:
                    return result
                stream_id = identifier + "-" + uuid4().hex
                self.streams.file_receiver(stream_id, {"session_id": identifier, "path": payload["path"],
                                                       "offset": result["offset"]})
                return {**result, "stream_id": stream_id}
            if action == "verify":
                return {"verified": service.verify_file(identifier, payload["path"])}
            if action == "commit":
                service.finish(identifier)
                return {}
            raise ValueError("unsupported dataset import action")
        if operation == Operation.CLEANUP:
            for item in payload.get("volumes", []):
                self.slot("storage").delete(item["id"], item["owner"])
            for item in payload.get("images", []):
                self.slot("registry").delete(item["id"], item["owner"])
            for stream_id in payload.get("streams", []):
                self.streams.remove(stream_id)
            for identifier in payload.get("dataset_sessions", []):
                self.dataset_service().cleanup(identifier)
            return {}
        raise SlotError("command.unsupported", operation=operation.value)
