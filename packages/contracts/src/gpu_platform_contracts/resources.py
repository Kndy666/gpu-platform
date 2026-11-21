from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import Contract
from .texts import LocalizedText

Quantity = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class ResourceDefinition(Contract):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    unit: str
    label: LocalizedText
    scope: Literal["cluster", "node"] = "cluster"
    integral: bool = False
    requestable: bool = True
    accounting: Literal["task", "volume", "image"] = "task"
    volume_kind: Literal["home", "work"] | None = None
    allocation_group: str | None = None
    allocation_field: str | None = None
    per_allocation: bool = False
    default: Quantity | dict[Literal["interactive", "batch"], Quantity] | None = None
    minimum: Quantity | None = None
    maximum: Quantity | None = None
    step: Quantity | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_constraints(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Resource minimum exceeds its maximum")
        if self.volume_kind is not None and self.accounting != "volume":
            raise ValueError("Volume kinds require volume accounting")
        if (self.allocation_field or self.per_allocation) and not self.allocation_group:
            raise ValueError("Allocation constraints require an allocation group")
        return self


class ResourceQuotaRule(Contract):
    source: ResourceDefinition
    basis: Literal["total", "per_allocation"] = "total"
    divisor: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)] = Decimal(1)

    @model_validator(mode="after")
    def valid_basis(self):
        if self.basis == "per_allocation" and not self.source.per_allocation:
            raise ValueError("Per-allocation quota conversions require a per-allocation source")
        return self


class StoragePoolUsage(Contract):
    name: str = Field(min_length=1)
    capacity_gib: Quantity
    free_gib: Quantity
    reserved_gib: Quantity
    buffer_percent: int = Field(default=5, ge=0, le=100)
    healthy: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


class ResourceUsage(Contract):
    total: dict[str, Quantity] = Field(default_factory=dict)
    used: dict[str, Quantity] = Field(default_factory=dict)
    pools: list[StoragePoolUsage] = Field(default_factory=list)


class GPUDevice(Contract):
    id: str
    name: str
    compute_percent: Quantity = Decimal(100)
    memory_gib: Quantity
    used_compute_percent: Quantity = Decimal(0)
    used_memory_gib: Quantity = Decimal(0)


class GPURequest(Contract):
    device_id: str
    compute_percent: Annotated[Decimal, Field(gt=0, le=100, allow_inf_nan=False)]
    memory_gib: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
