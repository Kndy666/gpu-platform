"""Stable serialized model defaults."""

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
