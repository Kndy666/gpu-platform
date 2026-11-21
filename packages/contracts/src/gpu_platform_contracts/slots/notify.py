from abc import ABC, abstractmethod

from ..module import Module
from ..texts import Message


class Notify(Module, ABC):
    @abstractmethod
    def recipient_fields(self) -> dict[str, dict]: ...

    @abstractmethod
    def send(self, recipient: dict[str, str], event: Message, language: str) -> None: ...
