from abc import ABC, abstractmethod
from datetime import datetime

from ..environment import StandardEnvironment
from ..assessment import ResourceAssessment, ResourceIntent
from ..handoffs import Image, Snapshot, TaskInputs
from ..messages import TaskSpec, TaskStatus
from ..module import Module
from ..resources import ResourceUsage


class Runtime(Module, ABC):
    environment = StandardEnvironment()

    @abstractmethod
    def assess(self, intent: ResourceIntent) -> ResourceAssessment: ...

    @abstractmethod
    def images(self) -> list[Image]: ...

    @abstractmethod
    def start(self, task: TaskSpec, inputs: TaskInputs) -> TaskStatus: ...

    @abstractmethod
    def stop(self, task_id: str) -> TaskStatus: ...

    @abstractmethod
    def status(self, task_id: str) -> TaskStatus: ...

    @abstractmethod
    def extend(self, task_id: str, expires_at: datetime) -> TaskStatus: ...

    @abstractmethod
    def logs(self, task_id: str, tail: int = 200) -> str: ...

    @abstractmethod
    def set_authorized_keys(self, task_id: str, keys: list[str]) -> None: ...

    @abstractmethod
    def list_tasks(self) -> list[TaskStatus]: ...

    @abstractmethod
    def acknowledge(self, task_id: str) -> None:
        """Stop reporting a fully cleaned terminal task; retain queryable history and logs."""
        ...

    @abstractmethod
    def resources(self) -> ResourceUsage: ...

    @abstractmethod
    def snapshot(self, task_id: str, owner: str) -> Snapshot: ...

    @abstractmethod
    def release_snapshot(self, snapshot_id: str) -> None: ...

    @abstractmethod
    def expire(self, now: datetime) -> list[TaskStatus]: ...
