"""Configuration accepted by the registry implementation."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from gpu_platform_contracts.base import Contract


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

    @field_validator("url")
    @classmethod
    def https_origin(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("A verified HTTPS registry origin is required")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid registry port")
        return value
