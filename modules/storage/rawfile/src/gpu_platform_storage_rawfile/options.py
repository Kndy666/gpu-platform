"""Configuration for physical Rawfile pools and copy operations."""

from pydantic import Field
from gpu_platform_contracts.base import Contract


class Pool(Contract):
    name: str = Field(min_length=1)
    path: str = Field(pattern=r"^/")
    storage_class: str = Field(min_length=1)
    buffer_percent: float = Field(default=5, ge=0, lt=100)


class Options(Contract):
    namespace: str = Field(default="gpu-workloads", min_length=1)
    pools: list[Pool] = Field(default_factory=list)
    storage_class: str = Field(default="rawfile", min_length=1, description="Storage class for directly configured existing infrastructure.")
    copy_image: str = Field(default="debian:bookworm-slim", min_length=1)
