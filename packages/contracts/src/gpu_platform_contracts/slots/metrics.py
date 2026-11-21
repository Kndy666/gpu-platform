from abc import ABC, abstractmethod

from ..module import Module


class Metrics(Module, ABC):
    @abstractmethod
    def sample(self) -> dict[str, float | str | None]: ...
