from abc import ABC, abstractmethod

from ..module import Module
from ..texts import Message
from ..base import Contract


IN_APP_CHANNEL = "inapp"


class InboxDelivery(Contract):
    title: str
    content: str


class Notify(Module, ABC):
    @abstractmethod
    def recipient_fields(self) -> dict[str, dict]: ...

    @abstractmethod
    def send(self, recipient: dict[str, str], event: Message, language: str) -> InboxDelivery | None: ...
