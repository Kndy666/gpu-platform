from importlib.metadata import entry_points
from pathlib import Path

from gpu_platform_contracts.config import HostConfig
from gpu_platform_contracts.messages import compatible_version
from gpu_platform_contracts.module import Module, ModuleContext
from gpu_platform_contracts.slots import SLOT_CONTRACTS


def load_slots(host: HostConfig, node_id: str, state_directory: Path, event_sink=None) -> dict[str, Module]:
    slots: dict[str, Module] = {}
    for slot, selection in host.slots.items():
        if selection is None or slot in {"auth", "notify", "transport", "entry"}:
            continue
        entries = tuple(entry_points(group=f"gpu_platform.slots.{slot}", name=selection.module))
        if len(entries) != 1:
            raise ValueError(f"module entry point is ambiguous or missing: {slot}/{selection.module}")
        implementation = entries[0].load()
        if not issubclass(implementation, SLOT_CONTRACTS[slot]):
            raise TypeError(f"module does not implement slot: {slot}/{selection.module}")
        if (
            implementation.descriptor.slot != slot
            or implementation.descriptor.side not in {"node", "both"}
            or not compatible_version(implementation.descriptor.api_version)
        ):
            raise ValueError(f"incompatible module: {slot}/{selection.module}")
        options = {**implementation.validate_options(selection.options), "architecture": host.architecture}
        context = ModuleContext(node_id=node_id, options=options, state_directory=state_directory)
        if event_sink:
            context.bind_events(event_sink)
        context.state_directory.mkdir(parents=True, exist_ok=True)
        slots[slot] = implementation(context)
    if not {"runtime", "storage"}.issubset(slots):
        raise ValueError("runtime and storage slots are required")
    return slots
