"""Versioned data passed between independently replaceable slots."""

from typing import Any, Literal
from pathlib import Path

from pydantic import Field

from .base import Contract
from .resources import GPURequest, Quantity

HANDOFF_VERSION = "1.0"


class Handoff(Contract):
    version: str = HANDOFF_VERSION
    form: str
    node_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegistryTrust(Handoff):
    """Host installation handoff for an OCI registry's verified TLS endpoint."""

    form: Literal["oci-registry"] = "oci-registry"
    endpoint: str = Field(pattern=r"^[A-Za-z0-9.\[\]:-]+$")
    ca_file: Path


class Volume(Handoff):
    id: str
    owner: str
    size_gib: Quantity
    location: str
    read_only: bool = False
    mount_path: str = "/workspace"


class Image(Handoff):
    id: str
    owner: str
    reference: str
    architecture: Literal["amd64", "arm64"]
    size_bytes: int = Field(default=0, ge=0)
    digest: str = ""
    credentials: dict[str, str] = Field(default_factory=dict, repr=False)


class GPUWorker(Contract):
    """An isolated accelerator service sharing only its client libraries with the workspace."""

    image: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    mounts: list[dict[str, Any]] = Field(default_factory=list)
    client_directory: str = Field(pattern=r"^/[A-Za-z0-9_./-]+$")
    ready_file: str = Field(pattern=r"^/[A-Za-z0-9_./-]+$")


class GPUAllocation(Handoff):
    id: str
    task_id: str
    devices: list[GPURequest]
    environment: dict[str, str] = Field(default_factory=dict)
    mounts: list[dict[str, Any]] = Field(default_factory=list)
    worker: GPUWorker | None = None


class GPURuntime(Handoff):
    """Host OCI runtime supplied by an accelerator module during installation."""

    binary: str = Field(pattern=r"^/[A-Za-z0-9_./-]+$")
    prestart_hook: str | None = Field(default=None, pattern=r"^/[A-Za-z0-9_./-]+$")


class Snapshot(Handoff):
    id: str
    owner: str
    task_id: str
    location: str
    size_bytes: int = Field(default=0, ge=0)


class ReadOnlyDirectory(Handoff):
    path: str
    mount_path: str
    read_only: Literal[True] = True


class PortConnection(Contract):
    version: str = HANDOFF_VERSION
    host: str
    port: int = Field(ge=1, le=65535)


class LoginAddress(Contract):
    host: str
    port: int = Field(ge=1, le=65535)
    username: str
    task_id: str


class VolumeRequest(Contract):
    id: str
    size_gib: Quantity
    kind: Literal["home", "work"]
    mount_path: str


class TaskInputs(Contract):
    volumes: list[Volume] = Field(default_factory=list)
    image: Image | None = None
    gpu: GPUAllocation | None = None
    datasets: list[ReadOnlyDirectory] = Field(default_factory=list)
