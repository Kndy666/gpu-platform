"""Compatibility validation before any installation side effects."""
from __future__ import annotations

from pathlib import Path
import inspect
from typing import Any, Callable

from gpu_platform_contracts.config import PlatformConfig

from .config import effective_config, resolve_host
from .host.system import CheckIssue, HostContext, check_system, run
from .modules import SelectedModule, discover, selected_modules
from .order import installation_order


class CompatibilityError(ValueError):
    def __init__(self, issues: list[CheckIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{issue.code}: {issue.parameters}" for issue in issues))


def module_context(config: PlatformConfig, name: str, module: SelectedModule,
                   runner: Callable[..., Any] = run, facts: dict[str, Any] | None = None,
                   installation_handoffs: dict[str, list[dict[str, Any]]] | None = None) -> HostContext:
    return HostContext(
        node_id=name,
        options=module.selection.options,
        state_directory=Path(config.cluster.state_directory),
        config=effective_config(config),
        host=resolve_host(config, name).model_dump(mode="json"),
        runner=runner,
        facts=facts or {},
        installation_handoffs=installation_handoffs if installation_handoffs is not None else {},
    )


def validate(config: PlatformConfig, name: str, *, loader: Callable[[str, str], Any] = discover,
             facts: dict[str, Any] | None = None, runner: Callable[..., Any] = run,
             machine_checks: bool = False) -> dict[str, SelectedModule]:
    host = resolve_host(config, name)
    issues = []
    if "node" in host.roles:
        if host.features.public_datasets and not Path(host.parameters.get("datasets", "/data/datasets")).is_absolute():
            issues.append(CheckIssue("feature.datasets_path_invalid", {"host": name}))
        for slot in ("runtime", "storage", "transport", "entry"):
            if host.slots.get(slot) is None:
                issues.append(CheckIssue("module.required", {"host": name, "slot": slot}))
    modules = selected_modules(config, host, loader)
    for key, module in modules.items():
        descriptor = module.descriptor
        if inspect.isabstract(module.implementation):
            issues.append(CheckIssue("module.incomplete", {"module": key}))
        target_side = "control" if key.startswith(("auth", "notify", "transport:", "entry:")) else "node"
        if descriptor.side not in {target_side, "both"}:
            issues.append(CheckIssue("module.side_incompatible", {"module": key, "side": target_side}))
        for slot, allowed in descriptor.requires.items():
            selected = modules.get(slot)
            if selected is None or selected.descriptor.name not in allowed:
                issues.append(CheckIssue("module.requirement_unavailable", {"module": key, "slot": slot,
                                                                             "allowed": sorted(allowed)}))

    # Optional providers participate when configured; storage and runtime are required above.
    for consumer, module in modules.items():
        for handoff, accepted in module.descriptor.accepts.items():
            providers = [item for key, item in modules.items()
                         if key != consumer and handoff in item.descriptor.provides
                         and (handoff not in {"port", "stream"} or key.partition(":")[2] == consumer.partition(":")[2])]
            if providers and not any(accepted & item.descriptor.provides[handoff] for item in providers):
                issues.append(CheckIssue("handoff.incompatible", {"consumer": consumer, "handoff": handoff}))

    for key, module in modules.items():
        if module.descriptor.slot == "transport":
            for handoff in ("port", "stream"):
                if not module.descriptor.provides.get(handoff):
                    issues.append(CheckIssue("handoff.transport_required", {"module": key, "handoff": handoff}))
        if module.descriptor.slot == "entry":
            transport_key = "transport" + (":" + key.partition(":")[2] if ":" in key else "")
            transport = modules.get(transport_key)
            if not transport or not (module.descriptor.accepts.get("port", set()) & transport.descriptor.provides.get("port", set())):
                issues.append(CheckIssue("handoff.entry_port_unavailable", {"module": key}))

    runtime = modules.get("runtime")
    registry = modules.get("registry")
    if "node" in host.roles:
        storage = modules.get("storage")
        if runtime and storage and not (
            runtime.descriptor.accepts.get("volume", set()) & storage.descriptor.provides.get("volume", set())
        ):
            issues.append(CheckIssue("handoff.required_volume_unavailable", {"host": name}))
        if host.features.save_environment:
            if not registry or not runtime or not (
                runtime.descriptor.provides.get("snapshot", set()) & registry.descriptor.accepts.get("snapshot", set())
            ):
                issues.append(CheckIssue("feature.save_environment_unavailable", {"host": name}))
        if host.features.public_datasets and runtime and not runtime.descriptor.accepts.get("readonly_directory"):
            issues.append(CheckIssue("feature.public_datasets_unavailable", {"host": name}))
        if host.features.image_sync and not registry:
            issues.append(CheckIssue("feature.image_sync_unavailable", {"host": name}))
        if host.features.dataset_sync and not host.features.public_datasets:
            issues.append(CheckIssue("feature.dataset_sync_unavailable", {"host": name}))
        if host.features.image_sync or host.features.dataset_sync:
            transport = modules.get("transport")
            if not transport or not transport.descriptor.provides.get("stream"):
                issues.append(CheckIssue("feature.sync_transport_unavailable", {"host": name}))

    for policy in config.control.sync.images:
        source = resolve_host(config, policy.source)
        for target_name in policy.targets:
            target = resolve_host(config, target_name)
            if source.architecture != target.architecture:
                issues.append(CheckIssue("sync.architecture_mismatch", {"source": policy.source, "target": target_name}))
            for participant_name, participant in ((policy.source, source), (target_name, target)):
                if "node" not in participant.roles or not participant.features.image_sync or not participant.slots.get("registry"):
                    issues.append(CheckIssue("sync.image_participant_unavailable", {"host": participant_name}))
    for dataset_policy in config.control.sync.datasets:
        for participant_name in [dataset_policy.source, *dataset_policy.targets]:
            participant = resolve_host(config, participant_name)
            if "node" not in participant.roles or not participant.features.dataset_sync or not participant.features.public_datasets:
                issues.append(CheckIssue("sync.dataset_participant_unavailable", {"host": participant_name}))
        if any(Path(value).is_absolute() or ".." in Path(value).parts for value in dataset_policy.paths):
            issues.append(CheckIssue("sync.invalid_dataset_path", {"source": dataset_policy.source}))
    try:
        installation_order({key: module.descriptor for key, module in modules.items()})
    except ValueError as error:
        issues.append(CheckIssue("module.dependency_invalid", {"reason": str(error)}))

    if machine_checks:
        if facts is None:
            from .host.system import inspect_host

            facts = inspect_host()
        issues.extend(check_system(facts))
        if facts.get("architecture") != host.architecture:
            issues.append(CheckIssue("host.architecture_mismatch", {"host": name, "configured": host.architecture,
                                                                    "actual": facts.get("architecture")}))
        for key, module in modules.items():
            for result in module.implementation.check_host(module_context(config, name, module, runner, facts)):
                if isinstance(result, CheckIssue):
                    issues.append(result)
                elif not result.ok:
                    issues.append(CheckIssue(result.message.code, {"module": key, **result.message.parameters}))
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise CompatibilityError(errors)
    return modules
