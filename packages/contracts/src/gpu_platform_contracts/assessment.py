"""Side-effect-free resource feasibility queries."""

from pydantic import Field
from typing import Literal

from .base import Contract
from .handoffs import VolumeRequest
from .resources import GPURequest, Quantity
from .texts import Message


class ResourceIntent(Contract):
    owner: str = ""
    resources: dict[str, Quantity] = Field(default_factory=dict)
    gpus: list[GPURequest] = Field(default_factory=list)
    volumes: list[VolumeRequest] = Field(default_factory=list)
    allocation_requests: dict[str, list[dict[str, Quantity | Literal["max"]]]] = Field(default_factory=dict)
    limits: dict[str, Quantity | None] = Field(default_factory=dict)
    per_allocation_limits: dict[str, Quantity | None] = Field(default_factory=dict)


class ResourceAssessment(Contract):
    feasible: bool
    quantities: dict[str, Quantity] = Field(default_factory=dict)
    gpus: list[GPURequest] = Field(default_factory=list)
    allocations: dict[str, list[dict[str, Quantity]]] = Field(default_factory=dict)
    reason: Message | None = None
