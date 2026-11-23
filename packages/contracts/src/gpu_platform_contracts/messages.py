"""Bounded wire messages; all user-visible details use event codes."""

import json
from enum import Enum
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from .base import Contract
from .events import ModuleEvent
from .environment import EnvironmentIdentity
from .handoffs import Image, VolumeRequest
from .module import API_VERSION, ModuleDescriptor
from .resources import GPUDevice, GPURequest, Quantity, ResourceUsage
from .texts import Message

MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class Operation(str, Enum):
    TASK_ACK = "task.ack"
    DATASETS_MANIFEST = "datasets.manifest"
    EVENTS_ACK = "events.ack"
    RESOURCE_ASSESS = "resource.assess"
    TASK_START = "task.start"
    TASK_STOP = "task.stop"
    TASK_STATUS = "task.status"
    TASK_EXTEND = "task.extend"
    TASK_LOGS = "task.logs"
    TASK_KEYS = "task.keys"
    PEER_KEYS = "peer.keys"
    PEER_LEASES = "peer.leases"
    PEER_PENDING = "peer.pending"
    PEER_SOURCE = "peer.source"
    PEER_TARGET = "peer.target"
    PEER_REJECT = "peer.reject"
    STORAGE_PREPARE = "storage.prepare"
    STORAGE_RESIZE = "storage.resize"
    STORAGE_DELETE = "storage.delete"
    STORAGE_COPY = "storage.copy"
    IMAGE_SAVE = "image.save"
    IMAGE_BUILD = "image.build"
    BUILD_CANCEL = "build.cancel"
    IMAGE_EXPORT = "image.export"
    IMAGE_IMPORT = "image.import"
    IMAGE_DELETE = "image.delete"
    DATASETS_LIST = "datasets.list"
    DATASETS_DOWNLOAD = "datasets.download"
    DATASETS_EXPORT = "datasets.export"
    DATASETS_IMPORT = "datasets.import"
    CLEANUP = "cleanup"
    CAPABILITIES = "capabilities"
    STATUS = "status"
    MAINTENANCE = "node.maintenance"


class TaskSpec(Contract):
    id: str
    node_id: str
    owner: str
    kind: Literal["interactive", "batch"]
    image: str
    image_owner: str | None = None
    resources: dict[str, Quantity] = Field(default_factory=dict)
    volumes: list[VolumeRequest] = Field(default_factory=list)
    gpus: list[GPURequest] = Field(default_factory=list)
    identity: EnvironmentIdentity
    expires_at: AwareDatetime | None = None
    command: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    public_datasets: list[str] = Field(default_factory=list)
    priority: int = 0
    login_port: int | None = Field(default=None, ge=1, le=65535)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_allocations(self):
        if len({volume.id for volume in self.volumes}) != len(self.volumes):
            raise ValueError("duplicate volume")
        if len({gpu.device_id for gpu in self.gpus}) != len(self.gpus):
            raise ValueError("duplicate GPU allocation")
        return self


TaskState = Literal["queued", "starting", "running", "ended", "failed"]
TERMINAL_STATES = frozenset({"ended", "failed"})
ACTIVE_STATES = frozenset({"queued", "starting", "running"})


class TaskStatus(Contract):
    id: str
    state: TaskState
    usage: dict[str, Quantity] = Field(default_factory=dict)
    error: Message | None = None
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    exit_code: int | None = None
    port: int | None = Field(default=None, ge=1, le=65535)


class Command(Contract):
    version: str = API_VERSION
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    operation: Operation
    payload: dict[str, Any] = Field(default_factory=dict)


class Reply(Contract):
    version: str = API_VERSION
    id: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: Message | None = None

    @model_validator(mode="after")
    def error_state(self):
        if self.ok and self.error is not None:
            raise ValueError("successful reply has an error")
        if not self.ok and self.error is None:
            raise ValueError("failed reply requires an error")
        return self


class CapabilityReport(Contract):
    node_id: str
    version: str = API_VERSION
    architecture: Literal["amd64", "arm64"]
    modules: list[ModuleDescriptor] = Field(default_factory=list)
    features: dict[str, bool] = Field(default_factory=dict)
    resources: ResourceUsage = Field(default_factory=ResourceUsage)
    gpus: list[GPUDevice] = Field(default_factory=list)
    gpu_explanation: Message | None = None
    images: list[Image] = Field(default_factory=list)


class SyncProgress(Contract):
    id: str
    state: Literal["queued", "running", "completed", "failed"]
    transferred_bytes: int = Field(default=0, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    error: Message | None = None


class NodeReport(Contract):
    node_id: str
    version: str = API_VERSION
    observed_at: AwareDatetime
    maintenance: bool = False
    tasks: list[TaskStatus] = Field(default_factory=list)
    usage: ResourceUsage = Field(default_factory=ResourceUsage)
    metrics: dict[str, float | str | None] = Field(default_factory=dict)
    sync: list[SyncProgress] = Field(default_factory=list)
    events: list[ModuleEvent] = Field(default_factory=list)


def encode_message(message: Contract) -> bytes:
    data = message.model_dump_json().encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds maximum size")
    return data


class MessageStream(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...


def read_message(stream: MessageStream) -> dict[str, Any]:
    data = stream.readline(MAX_MESSAGE_BYTES + 1)
    if len(data) > MAX_MESSAGE_BYTES or not data.endswith(b"\n"):
        raise ValueError("invalid message frame")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("message must be an object")
    return result
