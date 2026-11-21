"""Configuration accepted by the registry implementation."""

from typing import Literal
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from gpu_platform_contracts.base import Contract


class ImagePool(Contract):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=40)
    path: Path
    buffer_percent: int = Field(default=5, ge=0, le=99)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, path):
        if not path.is_absolute() or path == Path("/") or ".." in path.parts:
            raise ValueError("Use an absolute image pool directory on a data filesystem")
        return path


class Options(Contract):
    port: int = Field(default=5000, ge=1, le=65535)
    url: str = Field(default="https://127.0.0.1:5000", description="HTTPS origin; when omitted, use loopback and the configured port.")
    publish_address: str = Field(default="127.0.0.1", min_length=1)
    image: str = Field(default="", description="Container image; when omitted, use the bundled version for the host architecture.")
    container_name: str = Field(default="gpu-platform-registry", min_length=1)
    build_mirrors: list[str] = Field(default_factory=list, description="Builder-reachable registry authorities; defaults to the Compose registry service.")
    ca: str | Literal[True] = Field(default=True, description="CA file; when omitted, use the installed registry certificate.")
    architecture: Literal["amd64", "arm64"] = "amd64"
    budget_gib: int = Field(default=300, gt=0)
    pools: list[ImagePool] = Field(default_factory=list)
    existing_images_pool: str | None = None

    @model_validator(mode="after")
    def valid_pools(self):
        names = {pool.name for pool in self.pools}
        if len(names) != len(self.pools) or len({pool.path for pool in self.pools}) != len(self.pools):
            raise ValueError("Image pool names and directories must be unique")
        if self.existing_images_pool is not None and self.existing_images_pool not in names:
            raise ValueError("The existing image pool must match a configured pool")
        return self

    @field_validator("url")
    @classmethod
    def https_origin(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("A verified HTTPS registry origin is required")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid registry port")
        return value
