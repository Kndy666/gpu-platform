"""Mounts sampled by the host metrics module."""

from pydantic import Field
from gpu_platform_contracts.base import Contract


class Pool(Contract):
    name: str
    path: str


class Options(Contract):
    pools: list[Pool] = Field(default_factory=list)
