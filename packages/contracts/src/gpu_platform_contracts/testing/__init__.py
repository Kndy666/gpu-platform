"""Reusable behavioral suites. Implement the named fixtures in a module's tests.

Fixtures provision isolated resources; account IDs, ports, files, and hardware are
chosen by the harness. No deployment addresses or user-visible copy are asserted.
"""

from .suites import (
    AuthContract,
    BuildContract,
    EntryContract,
    GPUContract,
    MetricsContract,
    NotifyContract,
    RegistryContract,
    RuntimeContract,
    StorageContract,
    TransportContract,
)

__all__ = [
    "AuthContract",
    "BuildContract",
    "EntryContract",
    "GPUContract",
    "MetricsContract",
    "NotifyContract",
    "RegistryContract",
    "RuntimeContract",
    "StorageContract",
    "TransportContract",
]
