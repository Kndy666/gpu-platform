from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Callable

from gpu_platform_contracts.config import HostConfig, ModuleSelection, PlatformConfig
from gpu_platform_contracts.module import ModuleDescriptor


@dataclass
class SelectedModule:
    key: str
    selection: ModuleSelection
    implementation: Any
    descriptor: ModuleDescriptor


def discover(slot: str, name: str) -> Any:
    candidates = list(entry_points(group=f"gpu_platform.slots.{slot}", name=name))
    if len(candidates) != 1:
        raise ValueError(f"Expected one registered module for {slot}:{name}, found {len(candidates)}")
    return candidates[0].load()


def selected_modules(config: PlatformConfig, host: HostConfig,
                     loader: Callable[[str, str], Any] = discover) -> dict[str, SelectedModule]:
    selections: dict[str, tuple[str, ModuleSelection]] = {}
    if "node" in host.roles:
        selections.update({slot: (slot, selection) for slot, selection in host.slots.items()
                           if selection is not None and slot not in {"auth", "notify"}})
    if "control" in host.roles:
        selections["auth"] = ("auth", config.control.auth)
        selections.update({f"notify:{index}": ("notify", selection)
                           for index, selection in enumerate(config.control.notify)})
        from .config import resolve_host

        for name in config.hosts:
            remote = resolve_host(config, name)
            if "node" in remote.roles and "control" not in remote.roles:
                for slot in ("transport", "entry"):
                    selection = remote.slots.get(slot)
                    if selection is not None:
                        selections[f"{slot}:{name}"] = (slot, selection)
    modules = {}
    for key, (slot, selection) in selections.items():
        implementation = loader(slot, selection.module)
        selection = selection.model_copy(update={"options": implementation.validate_options(selection.options)})
        descriptor = implementation.descriptor
        if not isinstance(descriptor, ModuleDescriptor):
            descriptor = ModuleDescriptor.model_validate(descriptor)
        if descriptor.slot != slot or descriptor.name != selection.module:
            raise ValueError(f"Module registration does not match descriptor: {slot}:{selection.module}")
        modules[key] = SelectedModule(key, selection, implementation, descriptor)
    return modules
