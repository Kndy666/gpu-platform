"""Configuration metadata for the isolated BuildKit worker."""

from pydantic import Field
from gpu_platform_contracts.base import Contract


class Options(Contract):
    image: str = "moby/buildkit:v0.26.1-rootless"
    container_name: str | None = Field(default=None, description="Defaults to a stable name derived from the node identity")
    max_cpu: float | None = Field(default=None, gt=0, description="Maximum CPU cores per build; defaults to the host CPU count")
    max_memory_gib: float = Field(default=8, gt=0)
    max_build_seconds: int = Field(default=3600, gt=0)
    cache_budget_gib: int = Field(default=12, gt=0, description="Target BuildKit cache size after cleanup, in GiB")
    cache_max_age_hours: int = Field(default=4, gt=0, description="Maximum idle age of unused BuildKit cache, in hours")
    trusted_contexts: dict[str, str] = Field(default_factory=dict, description="Administrator-managed build directories; relative paths use the cluster state directory")
