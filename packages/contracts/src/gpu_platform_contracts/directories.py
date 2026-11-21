"""File manifests exchanged through node transports."""

from typing import Literal
from pathlib import PurePosixPath
from pydantic import Field, model_validator
from .base import Contract


class DirectoryEntry(Contract):
    kind: Literal["file", "directory"]
    modified: float = Field(ge=0, allow_inf_nan=False)
    size_bytes: int | None = Field(default=None, ge=0, strict=True)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def file_descriptor(self):
        if self.kind == "file" and (self.size_bytes is None or self.sha256 is None):
            raise ValueError("File size and checksum are required")
        return self


class DirectoryManifest(Contract):
    version: Literal[1] = 1
    paths: list[str]
    entries: dict[str, DirectoryEntry] = Field(max_length=200000)

    @model_validator(mode="after")
    def confined_paths(self):
        selections = [PurePosixPath(path) for path in self.paths]
        if not selections:
            raise ValueError("A directory selection is required")
        for name in [*self.paths, *self.entries]:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or "\x00" in name:
                raise ValueError("Invalid directory path")
        for name in self.entries:
            relative = PurePosixPath(name)
            if not any(
                parent == PurePosixPath(".") or parent == relative or parent in relative.parents
                for parent in selections
            ):
                raise ValueError("File is outside directory selection")
        return self
