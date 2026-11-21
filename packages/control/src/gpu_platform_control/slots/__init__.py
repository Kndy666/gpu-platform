"""Control-side module loading through the public entry-point contract."""

from importlib.metadata import entry_points
from gpu_platform_contracts.module import ModuleContext


def load(slot, selection, *, options=None):
    matches = list(entry_points(group="gpu_platform.slots." + slot, name=selection))
    if len(matches) != 1:
        raise LookupError(f"Expected one {slot} implementation for {selection}")
    implementation = matches[0].load()
    descriptor = implementation.descriptor
    if descriptor.slot != slot or descriptor.side not in {"control", "both"}:
        raise ValueError("Invalid control module descriptor")
    context = ModuleContext(options=implementation.validate_options(options or {}))
    bind_events(context, descriptor)
    return implementation(context)


def bind_events(context, descriptor):
    from gpu_platform_control.core.events.ingest import ingest_control_event
    context.bind_events(lambda event: ingest_control_event(event, descriptor))
