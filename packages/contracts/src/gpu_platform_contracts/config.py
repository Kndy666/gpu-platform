"""Cluster configuration. Loading and precedence belong to the installer."""

from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Contract
from .module import SlotName


class ModuleSelection(Contract):
    module: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class FeatureConfig(Contract):
    workspace_ssh: bool = True
    save_environment: bool = True
    public_datasets: bool = True
    image_sync: bool = True
    dataset_sync: bool = True


class HostDefaults(Contract):
    slots: dict[SlotName, ModuleSelection | None] = Field(default_factory=dict)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    parameters: dict[str, Any] = Field(default_factory=dict)


def node_roles() -> list[Literal["control", "node"]]:
    return ["node"]


class HostConfig(HostDefaults):
    roles: list[Literal["control", "node"]] = Field(default_factory=node_roles)
    architecture: Literal["amd64", "arm64"] = "amd64"
    address: str = "127.0.0.1"


class ClusterConfig(Contract):
    public_host: str = "localhost"
    default_language: Literal["en", "zh-hans"] = "en"
    ssh_ports: tuple[int, int] = (30022, 30121)
    control_image: str = "ghcr.io/kndy666/gpu-platform-control:0.4.0"
    panel_image: str = "ghcr.io/kndy666/gpu-platform-panel:0.4.0"
    node_image: str = "ghcr.io/kndy666/gpu-platform-node:0.4.0"
    state_directory: str = "/var/lib/gpu-platform"

    @model_validator(mode="after")
    def valid_ports(self):
        if not 1 <= self.ssh_ports[0] <= self.ssh_ports[1] <= 65535:
            raise ValueError("invalid port pool")
        return self


class DatasetSyncPolicy(Contract):
    source: str
    targets: list[str]
    paths: list[str] = Field(default_factory=list)
    interval_seconds: int = Field(default=3600, gt=0)
    bandwidth_bytes_per_second: int = Field(default=0, ge=0)


class ImageSyncPolicy(Contract):
    image: str
    source: str
    targets: list[str]
    bandwidth_bytes_per_second: int = Field(default=0, ge=0)


class SyncConfig(Contract):
    images: list[ImageSyncPolicy] = Field(default_factory=list)
    datasets: list[DatasetSyncPolicy] = Field(default_factory=list)


class MetricsHistoryConfig(Contract):
    max_mib: int = Field(default=512, ge=1)
    reserve_mib: int = Field(default=2048, ge=0)


class ControlConfig(Contract):
    auth: ModuleSelection = Field(default_factory=lambda: ModuleSelection(module="local"))
    notify: list[ModuleSelection] = Field(default_factory=list)
    groups: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    metrics_history: MetricsHistoryConfig = Field(default_factory=MetricsHistoryConfig)
    database: dict[str, Any] = Field(default_factory=lambda: {"engine": "sqlite", "name": "control.sqlite3"})


class PlatformConfig(Contract):
    cluster: ClusterConfig = Field(default_factory=ClusterConfig)
    defaults: HostDefaults = Field(default_factory=HostDefaults)
    control: ControlConfig = Field(default_factory=ControlConfig)
    hosts: dict[str, HostConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def topology(self):
        if sum("control" in host.roles for host in self.hosts.values()) != 1:
            raise ValueError("one control host is required")
        policies: list[ImageSyncPolicy | DatasetSyncPolicy] = [
            *self.control.sync.images,
            *self.control.sync.datasets,
        ]
        for policy in policies:
            if policy.source not in self.hosts or any(target not in self.hosts for target in policy.targets):
                raise ValueError("sync policy references an unknown host")
            if policy.source in policy.targets:
                raise ValueError("sync source cannot be its target")
        return self
