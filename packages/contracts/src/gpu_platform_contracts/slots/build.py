from abc import ABC, abstractmethod
from typing import Literal

from pydantic import Field

from ..base import Contract
from ..handoffs import Image
from ..module import Module
from ..resources import Quantity
from ..texts import Message


class BuildSpec(Contract):
    id: str
    owner: str
    description: str
    architecture: Literal["amd64", "arm64"]
    resources: dict[str, Quantity] = Field(default_factory=dict)
    context: str = ""
    images: list[Image] = Field(default_factory=list)


class BuildResult(Contract):
    id: str
    state: Literal["queued", "running", "completed", "failed"]
    image: Image | None = None
    logs: str = ""
    error: Message | None = None


class Build(Module, ABC):
    @abstractmethod
    def build(self, spec: BuildSpec) -> BuildResult: ...

    @abstractmethod
    def status(self, build_id: str) -> BuildResult: ...

    @abstractmethod
    def cancel(self, build_id: str) -> None: ...

    @abstractmethod
    def release_result(self, build_id: str) -> None:
        """Release terminal build artifacts after the caller has stored its result."""
        ...
