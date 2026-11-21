"""Order module installation using declared prerequisites and data providers."""
from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

from gpu_platform_contracts.module import ModuleDescriptor


# Snapshots flow back after a running environment exists; they are not startup dependencies.
PRIMARY_HANDOFFS = {"volume", "image", "gpu_allocation", "build_result", "readonly_directory", "port", "stream"}


def installation_order(descriptors: dict[str, ModuleDescriptor]) -> list[str]:
    graph: dict[str, set[str]] = {key: set() for key in descriptors}
    for consumer, descriptor in descriptors.items():
        for dependency in descriptor.dependencies:
            matches = [key for key, item in descriptors.items()
                       if key == dependency or item.slot == dependency or f"{item.slot}:{item.name}" == dependency]
            if not matches:
                raise ValueError(f"Missing module dependency: {consumer} -> {dependency}")
            graph[consumer].update(key for key in matches if key != consumer)
        for handoff, forms in descriptor.accepts.items():
            if handoff not in PRIMARY_HANDOFFS:
                continue
            for provider, item in descriptors.items():
                if handoff in {"port", "stream"} and provider.partition(":")[2] != consumer.partition(":")[2]:
                    continue
                if provider != consumer and forms & item.provides.get(handoff, set()):
                    graph[consumer].add(provider)
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ValueError(f"Module dependency cycle: {error.args[1]}") from error
