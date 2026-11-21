from abc import ABC, abstractmethod

from ..handoffs import Volume, VolumeRequest
from ..assessment import ResourceAssessment, ResourceIntent
from ..module import Module
from ..resources import Quantity, ResourceUsage


class Storage(Module, ABC):
    @abstractmethod
    def assess(self, intent: ResourceIntent) -> ResourceAssessment: ...

    @abstractmethod
    def create(self, owner: str, request: VolumeRequest) -> Volume: ...

    @abstractmethod
    def get(self, volume_id: str, owner: str) -> Volume: ...

    @abstractmethod
    def resize(self, volume_id: str, owner: str, size_gib: Quantity) -> Volume: ...

    @abstractmethod
    def delete(self, volume_id: str, owner: str) -> None: ...

    @abstractmethod
    def copy(self, volume_id: str, owner: str, destination: VolumeRequest) -> Volume: ...

    @abstractmethod
    def resources(self) -> ResourceUsage: ...
