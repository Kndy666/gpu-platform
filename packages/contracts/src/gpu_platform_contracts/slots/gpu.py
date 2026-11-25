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

    def reconcile(self, task_id: str, *, stopping: bool = False) -> None:
        """Recover or quiesce a task's accelerator service while retaining its reservation."""

    @abstractmethod
    def resources(self) -> ResourceUsage: ...

    def explanation(self) -> Message | None:
        return None
