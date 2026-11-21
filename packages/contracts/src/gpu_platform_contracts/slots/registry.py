from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from ..handoffs import Image, Snapshot
from ..module import Module
from ..resources import ResourceUsage


class Registry(Module, ABC):
    def clean_temporary_archives(self) -> None:
        """Reclaim temporary files whose import or export is no longer running."""

    def import_archive(self, image_id: str, owner: str, architecture: str, archive: Path) -> Image:
        with archive.open("rb") as stream:
            return self.import_image(image_id, owner, architecture, iter(lambda: stream.read(1024 * 1024), b""))

    @abstractmethod
    def build_images(self, owner: str, references: list[Image], build_id: str) -> list[Image]: ...

    @abstractmethod
    def release_build_access(self, build_id: str) -> None: ...

    @abstractmethod
    def get(self, image_id: str, owner: str) -> Image: ...

    @abstractmethod
    def store(self, source: Image | Snapshot, owner: str, image_id: str) -> Image: ...

    @abstractmethod
    def delete(self, image_id: str, owner: str) -> None: ...

    @abstractmethod
    def resources(self) -> ResourceUsage: ...

    @abstractmethod
    def export_image(self, image_id: str, owner: str) -> Iterable[bytes]: ...

    @abstractmethod
    def import_image(
        self, image_id: str, owner: str, architecture: str, chunks: Iterable[bytes]
    ) -> Image: ...
