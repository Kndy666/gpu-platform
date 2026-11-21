from abc import ABC, abstractmethod
from typing import Any

from pydantic import Field

from ..base import Contract
from ..module import Module


class Identity(Contract):
    subject: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    username: str
    email: str = ""
    display_name: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class Auth(Module, ABC):
    @abstractmethod
    def authenticate(self, credentials: dict[str, str]) -> Identity | None:
        """Return a stable provider-scoped identity after verifying credentials."""
