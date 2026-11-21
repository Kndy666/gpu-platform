"""Language-neutral events emitted by a selected module."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field

from .base import Contract


class ModuleEvent(Contract):
    id: UUID = Field(default_factory=uuid4)
    slot: str
    code: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: str | None = None
