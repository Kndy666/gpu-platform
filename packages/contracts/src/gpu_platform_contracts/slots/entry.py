from abc import ABC, abstractmethod
from typing import ClassVar

from ..handoffs import LoginAddress, PortConnection
from ..module import Module


class Entry(Module, ABC):
    requires_public_port: ClassVar[bool] = True

    @abstractmethod
    def open(
        self, task_id: str, username: str, connection: PortConnection, public_port: int | None
    ) -> LoginAddress: ...

    @abstractmethod
    def close(self, task_id: str) -> None: ...

    @abstractmethod
    def address(self, task_id: str) -> LoginAddress | None: ...
