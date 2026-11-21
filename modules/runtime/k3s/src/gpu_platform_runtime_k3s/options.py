from typing import Any, Literal

from pydantic import Field, IPvAnyAddress

from gpu_platform_contracts.base import Contract


class Network(Contract):
    pod_cidr: str = Field(default="10.42.0.0/16", min_length=1, description="Execution cluster Pod CIDR.")
    service_cidr: str = Field(default="10.43.0.0/16", min_length=1, description="Execution cluster Service CIDR.")
    registry_mirrors: dict[str, list[str]] = Field(default_factory=dict,
        description="Container registry endpoints used when bootstrapping the execution cluster.")


class CatalogImage(Contract):
    id: str = Field(min_length=1, description="Stable image identifier advertised by this runtime.")
    reference: str = Field(min_length=1, description="Image reference already present in the node image store.")
    form: Literal["node-local"] = "node-local"
    digest: str = ""
    size_bytes: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Options(Contract):
    api_bind_address: IPvAnyAddress | None = Field(default=None,
        description="Host address for the execution API listener; omitted uses loopback.")
    namespace: str = Field(default="workloads", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$",
        description="Kubernetes namespace containing workloads and persistent claims.")
    queue: str = Field(default="platform", min_length=1, description="Kueue LocalQueue and ClusterQueue name.")
    runtime_class: str = Field(default="workspace", min_length=1, description="CPU environment RuntimeClass.")
    gpu_runtime_class: str = Field(default="workspace-gpu", min_length=1, description="GPU environment RuntimeClass.")
    resource_view: str = Field(default="/var/lib/lxcfs", min_length=1,
        description="Host LXCFS resource-view path mounted read-only into environments.")
    nerdctl: str = Field(default="nerdctl", min_length=1, description="Executable used for environment snapshots.")
    images: list[CatalogImage] = Field(default_factory=list, description="Public node-local image catalog.")
    reserve_cpu: int = Field(default=4, ge=0, description="CPU cores reserved for host services.")
    reserve_memory_gib: int = Field(default=16, ge=0, description="Host service memory reservation in GiB.")
    network: Network | None = Field(default=None, description="Execution cluster network overrides.")
    registry_trust: dict[str, str] = Field(default_factory=dict,
        description="Registry authorities mapped to CA files; relative paths use the state directory.")
    bootstrap_directory: str = Field(default="bootstrap/environment", min_length=1,
        description="Standard environment build context directory under the state directory.")
    default_image: str = Field(default="gpu-platform/environment:0.4.0", min_length=1,
        description="Locally built environment reference when registry and build slots are absent.")
