from typing import Literal

from pydantic import Field

from .base import Contract


class EnvironmentIdentity(Contract):
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    uid: int = Field(ge=1)
    gid: int = Field(ge=1)
    ssh_keys: list[str] = Field(default_factory=list)


class StandardEnvironment(Contract):
    ssh: Literal[True] = True
    vscode: Literal[True] = True
    isolated_identity: Literal[True] = True
    home_directory: Literal[True] = True
    work_directory: Literal[True] = True
    docker: Literal[True] = True
    visible_resource_limits: Literal[True] = True
    read_only_public_data: Literal[True] = True
