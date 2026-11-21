from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Iterable

from ..handoffs import PortConnection
from ..messages import Command, Reply
from ..module import Module


class Transport(Module, ABC):
    @abstractmethod
    def request(self, endpoint: str, command: Command) -> Reply: ...

    @abstractmethod
    def online(self, endpoint: str) -> bool: ...

    @abstractmethod
    def send_stream(self, endpoint: str, stream_id: str, chunks: Iterable[bytes]) -> int: ...

    @abstractmethod
    def receive_stream(self, endpoint: str, stream_id: str) -> Iterable[bytes]: ...

    @abstractmethod
    def open_port(self, endpoint: str, port: int) -> AbstractContextManager[PortConnection]: ...
