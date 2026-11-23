"""Typed API request and response data used to generate OpenAPI."""

from __future__ import annotations

from datetime import date as Date, datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationReceiverRequest(Input):
    channel: str
    values: dict[str, str]


class NodeQuery(BaseModel):
    node: Annotated[int, Field(ge=1)] | None = None


class PageQuery(BaseModel):
    page: Annotated[int, Field(ge=1, le=1000000)] = 1
    page_size: Annotated[int, Field(ge=1, le=100)] = 20


class NodePageQuery(NodeQuery, PageQuery):
    pass


class TaskQuery(NodePageQuery):
    scope: Literal["own", "all"] = "own"
    mode: Literal["interactive", "batch"] | None = None
    status: str | None = None
    purpose: str | None = None


class ContainerQuery(NodePageQuery):
    q: str = ""
    mode: Literal["interactive", "batch"] | None = None
    status: str | None = None


class ImageQuery(NodePageQuery):
    kind: Literal["permanent", "temporary"] | None = None
    available: bool = False


class SearchQuery(PageQuery):
    q: str = ""


class DatasetQuery(NodePageQuery):
    path: str = ""
    q: str = ""


class DownloadQuery(NodeQuery):
    path: str


class TimelineQuery(NodeQuery):
    date: Date | None = None


class LogQuery(BaseModel):
    tail: Annotated[int, Field(ge=1, le=3000)] = 200


class ApprovalQuery(PageQuery):
    kind: Literal["tasks", "extensions"] = "tasks"


class AuditQuery(PageQuery):
    action: str | None = None


class OpenData(BaseModel):
    model_config = ConfigDict(extra="allow")


class TokenIssueRequest(Input):
    name: Annotated[str, Field(max_length=100)] | None = None
    expires_in: (
        Annotated[int, Field(ge=0, le=3153600000, description="Lifetime in seconds; 0 means permanent.")]
        | None
    ) = 604800


class TokenRequest(Input):
    username: str
    password: str
    name: Annotated[str, Field(max_length=100)] | None = None
    expires_in: (
        Annotated[
            int,
            Field(
                ge=0,
                le=3153600000,
                description="Lifetime in seconds; 0 means permanent. expires_at is null for permanent credentials.",
            ),
        ]
        | None
    ) = 604800


class PasswordRequest(Input):
    old_password: str
    new_password1: str
    new_password2: str


class KeyRequest(Input):
    name: str
    public_key: str


class KeyUpdate(Input):
    name: str | None = None
    enabled: bool | None = None


class NodeRequest(Input):
    name: str | None = None
    enabled: bool | None = None
    maintenance: bool | None = None


class TaskResourceExtensions(Input):
    resources: dict[str, Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict)
    allocations: dict[str, list[dict[str, Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] | Literal["max"]]]] = Field(default_factory=dict)
    volume_sizes: dict[Literal["home", "work"], Annotated[int, Field(ge=1)]] = Field(default_factory=dict)


class TaskRequest(Input):
    node: Annotated[int, Field(ge=1)] | None = None
    purpose: str
    mode: Literal["interactive", "batch"] | None = "interactive"
    image: UUID | None = None
    source_kind: Literal["image", "environment"] | None = None
    source_environment: (
        Annotated[
            UUID,
            Field(
                description="Own running development environment, with or without GPUs. Save its image, release it, then run the requested command."
            ),
        ]
        | None
    ) = None
    gpu: Annotated[int, Field(ge=0, description="Number of distinct physical GPUs")] | None = None
    gpu_requests: (
        Annotated[
            list[GPURequest],
            Field(
                max_length=64,
                description="One entry per distinct physical GPU, in CUDA device order. gpu may be omitted and is inferred from this list.",
            ),
        ]
        | None
    ) = None
    cpu: Annotated[int, Field(ge=1)] | None = None
    memory_gib: Annotated[int, Field(ge=1)] | None = None
    workspace_gib: (
        Annotated[
            int,
            Field(
                ge=1,
                description="When using an environment, omitted means its existing work volume size. A changed size creates a new volume and copies data after the source stops; the destination must fit all data.",
            ),
        ]
        | None
    ) = None
    starts_at: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    ) = None
    ends_at: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    ) = None
    command: str | None = None
    working_dir: str | None = "/workspace"
    environment: dict[str, str] | None = None
    notes: str | None = None
    extensions: TaskResourceExtensions | None = None


class ReviewRequest(Input):
    action: Literal["approve", "reject", "stop", "extend"]
    reason: str | None = None
    priority: Literal[0, 100] | None = None
    ends_at: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    ) = None


class ExtensionRequest(Input):
    ends_at: Annotated[
        datetime,
        Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
    ]
    reason: str


class ExtensionReviewRequest(Input):
    action: Literal["approve", "reject"]
    reason: str | None = None


class NotificationSettingsRequest(Input):
    enabled: bool | None = None
    receivers: dict[str, dict[str, str]] | None = None
    events: list[str] | None = None
    reminder_minutes: Annotated[int, Field(ge=1, le=1440)] | None = None


class BuildRequest(Input):
    node: Annotated[int, Field(ge=1)] | None = None
    name: str
    dockerfile: str
    context_git: str | None = None


class ResourcePolicyExtensions(Input):
    resources: dict[str, Annotated[Decimal, Field(ge=0)] | None] = Field(default_factory=dict)
    per_allocation: dict[str, Annotated[Decimal, Field(ge=0)] | None] = Field(default_factory=dict)


class MemberRequest(Input):
    extensions: ResourcePolicyExtensions | None = None
    username: str | None = None
    first_name: str | None = None
    email: str | None = None
    group: Annotated[int, Field(ge=1)] | None = None
    home_quota_gib: Annotated[int, Field(ge=1)] | None = None
    password: str | None = None
    is_active: bool | None = None
    is_staff: (
        Annotated[bool, Field(description="Editable by a superuser, matching the member form.")] | None
    ) = None
    unlimited: bool | None = None
    extension_limit: Annotated[int, Field(ge=0)] | None = None
    approval_mode: Literal["manual", "automatic"] | None = None
    gpu_compute_percent: Annotated[int, Field(ge=0)] | None = None
    gpu_memory_gib: Annotated[int, Field(ge=0)] | None = None
    gpu_compute_per_card_percent: Annotated[int, Field(ge=0)] | None = None
    gpu_memory_per_card_gib: Annotated[int, Field(ge=0)] | None = None
    cpu: Annotated[int, Field(ge=1)] | None = None
    memory_gib: Annotated[int, Field(ge=1)] | None = None
    workspace_gib: Annotated[int, Field(ge=1)] | None = None
    image_gib: Annotated[int, Field(ge=1)] | None = None
    concurrent_tasks: Annotated[int, Field(ge=1)] | None = None
    interactive_hours: Annotated[int, Field(ge=1)] | None = None


class GroupRequest(Input):
    home_size_gib: Annotated[int, Field(ge=1)] | None = None
    extensions: ResourcePolicyExtensions | None = None
    name: str | None = None
    approval_mode: Literal["manual", "automatic"] | None = None
    extension_limit: Annotated[int, Field(ge=0)] | None = 1
    unlimited: (
        Annotated[
            bool,
            Field(
                description="true removes compute and task limits. Work volume capacity remains required; image storage remains configurable."
            ),
        ]
        | None
    ) = None
    make_default: bool | None = None
    cpu: Annotated[int, Field(ge=1)] | None = None
    memory_gib: Annotated[int, Field(ge=1)] | None = None
    workspace_gib: Annotated[int, Field(ge=1)] | None = None
    image_gib: Annotated[int, Field(ge=1)] | None = None
    concurrent_tasks: Annotated[int, Field(ge=1)] | None = None
    interactive_hours: Annotated[int, Field(ge=1)] | None = None
    gpu_compute_percent: (
        Annotated[
            int,
            Field(
                ge=0,
                description="Total compute quota in percentage points; 150 permits 100% + 50% across GPUs",
            ),
        ]
        | None
    ) = None
    gpu_memory_gib: Annotated[int, Field(ge=0, description="Total VRAM quota in GiB")] | None = None
    gpu_compute_per_card_percent: (
        Annotated[int, Field(ge=0, le=100, description="Maximum compute share of each requested GPU")] | None
    ) = None
    gpu_memory_per_card_gib: (
        Annotated[int, Field(ge=0, description="Maximum VRAM of each requested GPU in GiB")] | None
    ) = None


class SettingsRequest(Input):
    workspace_retention_days: Annotated[int, Field(ge=1)] | None = None
    snapshot_hours: Annotated[int, Field(ge=1)] | None = None
    failed_snapshot_hours: Annotated[int, Field(ge=1)] | None = None


class EmptyRequest(Input):
    pass


class GPURequest(Input):
    compute_percent: Annotated[int, Field(ge=1, le=100)] | Literal["max"]
    memory_gib: Annotated[Decimal, Field(gt=0, multiple_of=Decimal(1) / 1024, allow_inf_nan=False)] | Literal["max"]


class ResolvedGPURequest(Input):
    compute_percent: Annotated[int, Field(ge=1, le=100)]
    memory_gib: Annotated[float, Field(gt=0)]


class GPURequestLimits(Input):
    compute_per_card_percent: Annotated[int, Field(ge=0, le=100)] | None = None
    memory_per_card_gib: Annotated[float, Field(ge=0)] | None = None
    compute_total_percent: Annotated[int, Field(ge=0)] | None = None
    memory_total_gib: Annotated[float, Field(ge=0)] | None = None
    devices: list[GPURequestLimitsDevicesItem] | None = None


class GPURequestLimitsDevicesItem(Input):
    memory_gib: Annotated[float, Field(ge=0)]


class GPUAllocation(Input):
    device_id: str | None = None
    uuid: str | None = None
    index: Annotated[int, Field(ge=0)] | None = None
    slot: Annotated[int, Field(ge=0, description="Zero-based request and CUDA device index")] | None = None
    compute_percent: Annotated[int, Field(ge=1, le=100)] | None = None
    memory_gib: Annotated[float, Field(gt=0)] | None = None
    memory_mib: Annotated[int, Field(ge=1)] | None = None


class DevicePresentation(Input):
    device_label: str = "GPU"
    device_memory_label: str = "GPU memory"
    device_details_label: str = "GPU allocations"


class GPUDevice(DevicePresentation):
    extensions: dict[str, Any] = Field(default_factory=dict)
    node_id: Annotated[int, Field(ge=1)] | None = None
    node_name: str | None = None
    uuid: str | None = None
    index: Annotated[int, Field(ge=0)] | None = None
    name: str | None = None
    pci: str | None = None
    memory_mib: Annotated[int, Field(ge=1)] | None = None
    usable_memory_mib: Annotated[int, Field(ge=1)] | None = None
    ready: bool | None = None
    used_compute_percent: Annotated[int, Field(ge=0)] | None = None
    used_memory_mib: Annotated[int, Field(ge=0)] | None = None
    available_compute_percent: Annotated[int, Field(ge=0)] | None = None
    available_memory_gib: Annotated[float, Field(ge=0)] | None = None
    used_memory_gib: Annotated[float, Field(ge=0)] | None = None
    memory_total_gib: Annotated[float, Field(ge=0)] | None = None


class GPUInventoryResponse(Input):
    data: GPUInventoryResponseData


class GPUInventoryResponseData(Input):
    devices: list[GPUDevice] | None = None
    fresh: bool | None = None
    request_limits: GPURequestLimits | None = None
    explanation: str = ""


class CapacityMetric(OpenData):
    available: Annotated[float, Field(ge=0)] | None = None
    total: Annotated[float, Field(ge=0)] | None = None


class RequestCapacity(DevicePresentation):
    resources: list[dict[str, Any]] = Field(default_factory=list)
    fresh: bool | None = None
    cpu: CapacityMetric | None = None
    memory_gib: CapacityMetric | None = None
    storage_gib: Annotated[float, Field(ge=0)] | None = None
    workspace_gib: RequestCapacityWorkspaceGib | None = None
    gpu_quota: RequestCapacityGpuQuota | None = None
    gpus: list[RequestCapacityGpusItem] | None = None


class RequestCapacityWorkspaceGib(Input):
    available: Annotated[int, Field(ge=0, description="null means unlimited.")] | None = None
    total: Annotated[int, Field(ge=0, description="null means unlimited.")] | None = None


class RequestCapacityGpuQuota(Input):
    compute_percent: Annotated[int, Field(ge=0)] | None = None
    memory_gib: Annotated[int, Field(ge=0)] | None = None


class RequestCapacityGpusItem(Input):
    node_id: Annotated[int, Field(ge=1)] | None = None
    node_name: str | None = None
    index: Annotated[int, Field(ge=0)] | None = None
    ready: bool | None = None
    compute_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    memory_gib: Annotated[float, Field(ge=0)] | None = None
    memory_total_gib: Annotated[float, Field(ge=0)] | None = None


class Node(Input):
    id: Annotated[int, Field(ge=1)]
    name: str
    architecture: Literal["amd64", "arm64"]
    enabled: bool
    maintenance: bool
    state: str
    state_label: str
    last_seen: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    )
    error: str


class NodeResponse(Input):
    data: Node


class NodeListResponse(Input):
    data: NodeListResponseData


class NodeListResponseData(Input):
    items: list[Node] | None = None
    count: Annotated[int, Field(ge=0)] | None = None
    page: Annotated[int, Field(ge=1)] | None = None
    page_size: Annotated[int, Field(ge=1)] | None = None
    next_page: Annotated[int, Field(ge=1)] | None = None


class NodeResources(DevicePresentation):
    accelerator_resource_keys: list[str] = Field(default_factory=list)
    id: Annotated[int, Field(ge=1)] | None = None
    name: str | None = None
    state: str | None = None
    state_label: str | None = None
    resources: dict[str, Any] | None = None
    gpus: list[GPUDevice] | None = None
    model: str | None = None
    gpu_available: Annotated[float, Field(ge=0)] | None = None
    storage_tib: Annotated[float, Field(ge=0)] | None = None


class ResourcesResponse(Input):
    data: ResourcesResponseData


class ResourcesResponseData(OpenData):
    nodes: (
        Annotated[
            list[NodeResources],
            Field(description="Dashboard resource groups; node=ID limits this list to that node."),
        ]
        | None
    ) = None
    request_capacity: RequestCapacity | None = None


class TimelineRow(Input):
    id: str | None = None
    node_id: Annotated[int, Field(ge=1)] | None = None
    name: str | None = None
    detail: str | None = None


class TimelineEntry(OpenData):
    node_id: Annotated[int, Field(ge=1)] | None = None
    id: UUID | None = None
    title: str | None = None
    url: str | None = None
    state: Literal["reserved", "running", "finished"] | None = None
    start: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    ) = None
    end: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    ) = None
    left: float | None = None
    width: float | None = None
    gpu: Annotated[int, Field(ge=0)] | None = None
    cpu: Annotated[int, Field(ge=1)] | None = None
    memory: Annotated[int, Field(ge=1)] | None = None


class TimelineQueuedTask(Input):
    device_label: str = 'GPU'
    workspace_gib: int = 0
    gpu_requests: list[dict[str, Any]] = Field(default_factory=list)
    gpu_compute_total: float = 0
    gpu_memory_total_gib: float = 0
    node_id: Annotated[int, Field(ge=1)]
    id: UUID | None
    title: str
    url: str | None
    status: str
    status_label: str
    gpu: Annotated[int, Field(ge=0)]
    cpu: Annotated[int, Field(ge=1)]
    memory_gib: Annotated[int, Field(ge=1)]


class TimelineGroup(Input):
    id: Annotated[int, Field(ge=1)]
    name: str
    gpu_count: Annotated[int, Field(ge=0)]
    rows: list[TimelineRow]
    blocks: list[TimelineEntry]
    entries: list[TimelineEntry]
    queued: Annotated[
        list[TimelineQueuedTask],
        Field(
            description="Waiting tasks eligible to start now, ordered by priority then submission time. Empty on other dates. Other users see anonymous IDs and titles."
        ),
    ]


class TimelineResponse(Input):
    data: TimelineResponseData


class TimelineResponseData(Input):
    date: Date | None = None
    now: Annotated[float, Field(description="Current time as a percentage of the selected day.")] | None = (
        None
    )
    rows: list[TimelineRow] | None = None
    blocks: list[TimelineEntry] | None = None
    groups: list[TimelineGroup] | None = None


class TaskGPUResources(OpenData):
    gpu: Annotated[int, Field(ge=0)] | None = None
    gpu_requests: list[ResolvedGPURequest] | None = None
    gpu_compute_total: Annotated[int, Field(ge=0)] | None = None
    gpu_memory_total_gib: Annotated[float, Field(ge=0)] | None = None
    gpu_allocations: list[GPUAllocation] | None = None


class TaskResponse(Input):
    data: TaskGPUResources


class SSHConnection(Input):
    host: str | None = None
    port: Annotated[int, Field(ge=1)] | None = None
    user: str | None = None
    alias: str | None = None
    command: str | None = None
    config: str | None = None


class AdministratorSSHAccess(Input):
    ready: bool | None = None
    sudo: bool | None = None
    keys: list[AdministratorSSHAccessKeysItem] | None = None


class AdministratorSSHAccessKeysItem(Input):
    id: Annotated[int, Field(ge=1)] | None = None
    name: str | None = None
    fingerprint: str | None = None


class ConnectionResponse(Input):
    data: ConnectionResponseData


class InternalSSH(Input):
    alias: str
    command: str


class InternalSSHPeer(Input):
    name: str
    command: str
    reachable: bool


class ConnectionResponseData(Input):
    internal_ssh: InternalSSH | None = None
    internal_ssh_peers: list[InternalSSHPeer] | None = None
    status: str | None = None
    connection: SSHConnection | None = None
    administrator_access: AdministratorSSHAccess | None = None


class GroupDelete(Input):
    destination: Annotated[int, Field(ge=1)] | None = None


class MetricFields(Input):
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def project_module_fields(cls, value):
        if not isinstance(value, dict):
            return value
        extra = {key: item for key, item in value.items() if key not in cls.model_fields}
        if not extra:
            return value
        fields = {key: item for key, item in value.items() if key in cls.model_fields}
        fields["extensions"] = {**fields.get("extensions", {}), **extra}
        return fields


class PerformanceMetrics(MetricFields):
    version: int | None = None
    sampled_at: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    ) = None
    interval_seconds: Annotated[int, Field(ge=1)] | None = None
    cpu: PerformanceMetricsCpu | None = None
    memory: PerformanceMetricsMemory | None = None
    gpus: list[PerformanceMetricsGpusItem] | None = None
    disks: list[PerformanceMetricsDisksItem] | None = None
    network: list[PerformanceMetricsNetworkItem] | None = None
    errors: list[str] | None = None


class PerformanceMetricsCpu(MetricFields):
    percent: Annotated[float, Field(ge=0, le=100)] | None = None
    cores: Annotated[int, Field(ge=1)] | None = None
    load: list[float] | None = None
    temperature_c: float | None = None
    temperature_sensors: list[PerformanceMetricsCpuTemperatureSensorsItem] | None = None


class PerformanceMetricsCpuTemperatureSensorsItem(MetricFields):
    name: str | None = None
    temperature_c: float | None = None


class PerformanceMetricsMemory(MetricFields):
    total_gib: Annotated[float, Field(ge=0)] | None = None
    used_gib: Annotated[float, Field(ge=0)] | None = None
    available_gib: Annotated[float, Field(ge=0)] | None = None
    percent: Annotated[float, Field(ge=0)] | None = None


class PerformanceMetricsGpusItem(MetricFields):
    index: Annotated[int, Field(ge=0)] | None = None
    uuid: str | None = None
    name: str | None = None
    utilization_percent: Annotated[float, Field(ge=0)] | None = None
    memory_used_mib: Annotated[float, Field(ge=0)] | None = None
    memory_total_mib: Annotated[float, Field(ge=0)] | None = None
    memory_percent: Annotated[float, Field(ge=0)] | None = None
    temperature_c: Annotated[float, Field(ge=0)] | None = None
    power_w: Annotated[float, Field(ge=0)] | None = None
    power_limit_w: Annotated[float, Field(ge=0)] | None = None
    fan_percent: Annotated[float, Field(ge=0)] | None = None


class PerformanceMetricsDisksItem(MetricFields):
    name: str | None = None
    total_gib: Annotated[float, Field(ge=0)] | None = None
    used_gib: Annotated[float, Field(ge=0)] | None = None
    available_gib: Annotated[float, Field(ge=0)] | None = None
    percent: Annotated[float, Field(ge=0)] | None = None


class PerformanceMetricsNetworkItem(MetricFields):
    name: str | None = None
    rx_mib_s: Annotated[float, Field(ge=0)] | None = None
    tx_mib_s: Annotated[float, Field(ge=0)] | None = None


class PerformanceResponse(Input):
    data: PerformanceResponseData


class PerformanceResponseData(Input):
    refresh_seconds: Annotated[int, Field(ge=1)] | None = None
    nodes: list[PerformanceResponseDataNodesItem] | None = None


class PerformanceResponseDataNodesItem(DevicePresentation):
    id: Annotated[int, Field(ge=1)] | None = None
    name: str | None = None
    architecture: str | None = None
    state: str | None = None
    fresh: bool | None = None
    status_label: str | None = None
    sampled_at: (
        Annotated[
            datetime,
            Field(description="ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai."),
        ]
        | None
    ) = None
    metrics: PerformanceMetrics | None = None


class Response(Input):
    data: Any


class Error(Input):
    error: ErrorError


class ErrorError(Input):
    code: str
    message: str
    fields: dict[str, Any] | None = None


class ImageSyncRequest(Input):
    target: Annotated[int, Field(ge=1)]
    bandwidth: Annotated[int, Field(ge=0)] | None = None


class DatasetSyncRequest(Input):
    source: Annotated[int, Field(ge=1)]
    target: Annotated[int, Field(ge=1)]
    paths: list[str] | None = None
    bandwidth: Annotated[int, Field(ge=0)] | None = None


class LanguageRequest(Input):
    language: Literal["en", "zh-hans", ""]


T = TypeVar("T")


class Page(OpenData, Generic[T]):
    items: list[T]
    count: int
    page: int
    page_size: int
    next_page: int | None


class Envelope(Input, Generic[T]):
    data: T


class GroupData(OpenData):
    home_size_gib: Annotated[int, Field(ge=1)] | None = None
    extensions: ResourcePolicyExtensions | None = None
    id: int
    name: str
    is_default: bool
    unlimited: bool
    extension_limit: int
    approval_mode: Literal["manual", "automatic"]
    cpu: int | None = None
    memory_gib: int | None = None
    gpu_compute_percent: int | None = None
    gpu_memory_gib: int | None = None
    gpu_compute_per_card_percent: int | None = None
    gpu_memory_per_card_gib: int | None = None
    workspace_gib: int | None = None
    image_gib: int | None = None
    concurrent_tasks: int | None = None
    interactive_hours: int | None = None
    member_count: int | None = None


class UserData(OpenData):
    id: int
    username: str
    first_name: str
    email: str
    is_active: bool
    is_staff: bool
    is_superuser: bool
    group: GroupData
    home_quota_gib: int
    resource_limits: dict[str, Any]
    effective_limits: GroupData
    extension_limit: int
    approval_mode: Literal["manual", "automatic"]
    permissions: dict[str, bool] | None = None
    usage: dict[str, int | float] | None = None


class VolumeData(OpenData):
    id: UUID
    user_id: int
    node_id: int
    node_name: str
    request_id: UUID | None
    kind: Literal["home", "work"]
    size_gib: int
    used_bytes: int | None
    location: str
    state: str
    retain_until: datetime | None
    notified_at: datetime | None
    deletion_error: str
    created_at: datetime
    pool_id: int
    pool_name: str
    username: str
    request_name: str | None
    busy: bool


class TaskData(TaskGPUResources):
    id: UUID
    purpose: str
    mode: Literal["interactive", "batch"]
    status: str
    cpu: int
    memory_gib: int
    workspace_gib: int
    node_id: int
    node_name: str
    user_id: int
    username: str
    image_id: UUID
    image_name: str
    source_environment_id: UUID | None
    extension_count: int
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    working_dir: str
    priority: int
    login_name: str
    home_dir: str
    status_label: str
    connection: SSHConnection | None
    stop_requested: bool
    exit_code: int | None
    error: str
    cleanup_complete: bool
    command: str | None = None
    environment: dict[str, str] | None = None
    notes: str | None = None
    review_reason: str | None = None
    source_environment_name: str | None = None
    volume: VolumeData | None = None
    log_directory: str | None = None
    can_stop: bool | None = None
    extensions: dict[str, Any]


class ImageData(OpenData):
    id: UUID
    name: str
    owner_id: int | None
    owner_name: str | None
    node_id: int
    node_name: str
    reference: str
    digest: str
    size_bytes: int
    architecture: Literal["amd64", "arm64"]
    shared: bool
    verified: bool
    temporary: bool
    state: str
    state_label: str
    recycle_after: datetime | None
    cleanup_error: str
    created_at: datetime


class BuildData(OpenData):
    id: UUID
    user_id: int
    node_id: int
    node_name: str
    name: str
    context_git: str
    status: str
    status_label: str
    artifact_id: UUID | None
    error: str
    created_at: datetime
    dockerfile: str | None = None
    log: str | None = None


class SyncData(OpenData):
    phase: Literal['queued', 'preparing', 'transferring', 'applying', 'cleaning', 'completed', 'failed']
    id: UUID
    kind: Literal["image", "datasets"]
    source: int
    target: int
    state: str
    image: UUID | None
    result_image: UUID | None
    paths: list[str]
    transferred_bytes: int
    total_bytes: int | None
    error: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


MODELS: dict[str, type[BaseModel]] = {
    "NotificationReceiverRequest": NotificationReceiverRequest,
    "TokenIssueRequest": TokenIssueRequest,
    "TokenRequest": TokenRequest,
    "PasswordRequest": PasswordRequest,
    "KeyRequest": KeyRequest,
    "KeyUpdate": KeyUpdate,
    "NodeRequest": NodeRequest,
    "TaskRequest": TaskRequest,
    "ReviewRequest": ReviewRequest,
    "ExtensionRequest": ExtensionRequest,
    "ExtensionReviewRequest": ExtensionReviewRequest,
    "NotificationSettingsRequest": NotificationSettingsRequest,
    "BuildRequest": BuildRequest,
    "MemberRequest": MemberRequest,
    "GroupRequest": GroupRequest,
    "SettingsRequest": SettingsRequest,
    "EmptyRequest": EmptyRequest,
    "GPURequest": GPURequest,
    "ResolvedGPURequest": ResolvedGPURequest,
    "GPURequestLimits": GPURequestLimits,
    "GPURequestLimitsDevicesItem": GPURequestLimitsDevicesItem,
    "GPUAllocation": GPUAllocation,
    "GPUDevice": GPUDevice,
    "GPUInventoryResponse": GPUInventoryResponse,
    "GPUInventoryResponseData": GPUInventoryResponseData,
    "CapacityMetric": CapacityMetric,
    "RequestCapacity": RequestCapacity,
    "RequestCapacityWorkspaceGib": RequestCapacityWorkspaceGib,
    "RequestCapacityGpuQuota": RequestCapacityGpuQuota,
    "RequestCapacityGpusItem": RequestCapacityGpusItem,
    "Node": Node,
    "NodeResponse": NodeResponse,
    "NodeListResponse": NodeListResponse,
    "NodeListResponseData": NodeListResponseData,
    "NodeResources": NodeResources,
    "ResourcesResponse": ResourcesResponse,
    "ResourcesResponseData": ResourcesResponseData,
    "TimelineRow": TimelineRow,
    "TimelineEntry": TimelineEntry,
    "TimelineQueuedTask": TimelineQueuedTask,
    "TimelineGroup": TimelineGroup,
    "TimelineResponse": TimelineResponse,
    "TimelineResponseData": TimelineResponseData,
    "TaskGPUResources": TaskGPUResources,
    "TaskResponse": TaskResponse,
    "SSHConnection": SSHConnection,
    "AdministratorSSHAccess": AdministratorSSHAccess,
    "AdministratorSSHAccessKeysItem": AdministratorSSHAccessKeysItem,
    "ConnectionResponse": ConnectionResponse,
    "ConnectionResponseData": ConnectionResponseData,
    "InternalSSH": InternalSSH,
    "InternalSSHPeer": InternalSSHPeer,
    "GroupDelete": GroupDelete,
    "PerformanceMetrics": PerformanceMetrics,
    "PerformanceMetricsCpu": PerformanceMetricsCpu,
    "PerformanceMetricsCpuTemperatureSensorsItem": PerformanceMetricsCpuTemperatureSensorsItem,
    "PerformanceMetricsMemory": PerformanceMetricsMemory,
    "PerformanceMetricsGpusItem": PerformanceMetricsGpusItem,
    "PerformanceMetricsDisksItem": PerformanceMetricsDisksItem,
    "PerformanceMetricsNetworkItem": PerformanceMetricsNetworkItem,
    "PerformanceResponse": PerformanceResponse,
    "PerformanceResponseData": PerformanceResponseData,
    "PerformanceResponseDataNodesItem": PerformanceResponseDataNodesItem,
    "Response": Response,
    "Error": Error,
    "ErrorError": ErrorError,
    "ImageSyncRequest": ImageSyncRequest,
    "DatasetSyncRequest": DatasetSyncRequest,
    "LanguageRequest": LanguageRequest,
}

for model in MODELS.values():
    model.model_rebuild()
