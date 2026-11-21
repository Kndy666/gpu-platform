"""Module metadata and lifecycle shared by hosts and external implementations."""

from pathlib import Path
from dataclasses import dataclass, field
import subprocess
from typing import Any, Callable, ClassVar, Literal

from pydantic import Field, PrivateAttr, model_validator

from .base import Contract
from .events import ModuleEvent
from .resources import ResourceDefinition, ResourceQuotaRule
from .texts import LocalizedText, Message

SLOT_NAMES = (
    "auth",
    "transport",
    "entry",
    "runtime",
    "storage",
    "registry",
    "build",
    "gpu",
    "metrics",
    "notify",
)
SlotName = Literal[
    "auth", "transport", "entry", "runtime", "storage", "registry", "build", "gpu", "metrics", "notify"
]
API_VERSION = "1.0"


def run_host(argv, **options):
    options.setdefault("check", True)
    options.setdefault("text", True)
    return subprocess.run(argv, **options)


@dataclass
class HostContext:
    """Public installation-hook context with injected host operations and provider outputs."""

    node_id: str
    options: dict[str, Any]
    state_directory: Path
    config: dict[str, Any]
    host: dict[str, Any]
    runner: Callable[..., Any] = run_host
    facts: dict[str, Any] = field(default_factory=dict)
    installation_handoffs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class ModuleDescriptor(Contract):
    name: str = Field(min_length=1)
    slot: SlotName
    version: str
    api_version: str = API_VERSION
    side: Literal["control", "node", "both"]
    provides: dict[str, set[str]] = Field(default_factory=dict)
    accepts: dict[str, set[str]] = Field(default_factory=dict)
    resources: list[ResourceDefinition] = Field(default_factory=list)
    quota_rules: dict[str, ResourceQuotaRule] = Field(default_factory=dict)
    events: dict[str, LocalizedText] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict, description="JSON Schema for the module options object")
    texts: dict[str, LocalizedText] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    requires: dict[str, set[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_quota_rules(self):
        resources = {resource.key: resource for resource in self.resources}
        for key, rule in self.quota_rules.items():
            if key not in resources or not resources[key].integral or rule.source.key in self.quota_rules:
                raise ValueError("Quota conversions require a declared integral resource and a distinct source")
        return self


class ModuleContext(Contract):
    node_id: str = "local"
    options: dict[str, Any] = Field(default_factory=dict)
    state_directory: Path = Path("/var/lib/gpu-platform")
    _event_sink: Callable[[ModuleEvent], None] | None = PrivateAttr(default=None)

    def bind_events(self, sink: Callable[[ModuleEvent], None]) -> None:
        self._event_sink = sink

    def publish(self, event: ModuleEvent) -> None:
        if self._event_sink is None:
            raise RuntimeError("Module event sink is unavailable")
        self._event_sink(event)


class CheckResult(Contract):
    ok: bool
    message: Message


class Module:
    descriptor: ClassVar[ModuleDescriptor]
    options_model: ClassVar[type[Contract] | None] = None

    @classmethod
    def validate_options(cls, options: dict[str, Any]) -> dict[str, Any]:
        if cls.options_model is None:
            return dict(options)
        return cls.options_model.model_validate(options).model_dump(mode="python", exclude_unset=True, exclude_none=True)

    def __init__(self, context: ModuleContext):
        self.context = context

    def emit(self, code: str, *, task_id: str | None = None, **parameters: Any) -> ModuleEvent:
        if code not in self.descriptor.events:
            raise ValueError("Module event code is not registered")
        event = ModuleEvent(slot=self.descriptor.slot, code=code, task_id=task_id, parameters=parameters)
        self.context.publish(event)
        return event

    @classmethod
    def check_host(cls, context: HostContext) -> list[CheckResult]:
        return []

    @classmethod
    def install_host(cls, context: HostContext) -> None:
        pass

    @classmethod
    def uninstall_host(cls, context: HostContext) -> None:
        pass

    @classmethod
    def install(cls, context: HostContext) -> None:
        pass

    @classmethod
    def uninstall(cls, context: HostContext) -> None:
        pass

    @classmethod
    def compose(cls, context: HostContext) -> dict[str, Any]:
        return {}


class SlotError(Exception):
    def __init__(self, code: str, **parameters: Any):
        self.message = Message(code=code, parameters=parameters)
        super().__init__(code)


class SlotPending(SlotError):
    def __init__(self, **parameters: Any):
        super().__init__("operation.pending", **parameters)
