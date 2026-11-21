from abc import ABC, abstractmethod

from ..handoffs import GPUAllocation
from ..assessment import ResourceAssessment, ResourceIntent
from ..module import Module
from ..resources import GPUDevice, GPURequest, ResourceUsage
from ..texts import Message


class GPU(Module, ABC):
    @abstractmethod
    def assess(self, intent: ResourceIntent) -> ResourceAssessment: ...

    @abstractmethod
    def inventory(self) -> list[GPUDevice]: ...

    @abstractmethod
    def lookup(self, task_id: str) -> GPUAllocation | None: ...

    @abstractmethod
    def allocate(self, task_id: str, requests: list[GPURequest]) -> GPUAllocation: ...

    @abstractmethod
    def release(self, allocation_id: str) -> None: ...

    @abstractmethod
    def resources(self) -> ResourceUsage: ...

    @abstractmethod
    def explanation(self) -> Message: ...
