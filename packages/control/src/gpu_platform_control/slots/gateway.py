"""Commands and streams shared by local and remote node transports."""

from pathlib import Path
from importlib.metadata import entry_points
import time
from gpu_platform_contracts.messages import Command, compatible_version
from gpu_platform_contracts.module import ModuleContext
from .configuration import cluster_config


class NodeCommandError(RuntimeError):
    def __init__(self, message):
        self.message = message
        super().__init__(message.code)


def wait_for_result(gateway, node, operation, *, timeout=3600, clock=time.monotonic, sleep=time.sleep, **payload):
    deadline = clock() + timeout
    while True:
        try:
            return gateway.request(node, operation, **payload)
        except NodeCommandError as exc:
            if exc.message.code != "operation.pending":
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("node_operation_timeout") from exc
            sleep(min(1, remaining))


class Gateway:
    def __init__(self, configuration=None, loader=None):
        self._configuration = configuration
        self.loader = loader or self._load
        self.instances = {}

    @property
    def configuration(self):
        return self._configuration or cluster_config()

    def _load(self, slot, selection, context):
        matches = list(entry_points(group="gpu_platform.slots." + slot, name=selection))
        if len(matches) != 1:
            raise LookupError(f"Expected one {slot} implementation for {selection}")
        implementation = matches[0].load()
        if implementation.descriptor.slot != slot or implementation.descriptor.side not in {
            "control",
            "both",
        }:
            raise ValueError("Invalid control module descriptor")
        context.options = implementation.validate_options(context.options)
        if slot == "entry":
            context.options.setdefault("public_host", self.configuration.cluster.public_host)
        from . import bind_events
        bind_events(context, implementation.descriptor)
        return implementation(context)

    def module(self, node, slot):
        host = self.configuration.hosts[node.name]
        selected = host.slots.get(slot)
        if "control" in host.roles and slot in {"transport", "entry"}:
            selection, options = "local", {}
        elif selected:
            selection, options = selected.module, dict(selected.options)
        else:
            raise LookupError(f"Missing {slot} for {node.name}")
        import json
        key = (node.name, slot, selection, json.dumps(options, sort_keys=True))
        if key in self.instances:
            return self.instances[key]
        context = ModuleContext(
            node_id=node.name,
            options=options,
            state_directory=Path(self.configuration.cluster.state_directory),
        )
        self.instances[key] = self.loader(slot, selection, context)
        return self.instances[key]

    def endpoint(self, node):
        host = self.configuration.hosts[node.name]
        return "" if "control" in host.roles else host.address

    def request(self, node, operation, **payload):
        command = Command(operation=operation, payload=payload)
        reply = self.module(node, "transport").request(self.endpoint(node), command)
        if reply.id != command.id or not compatible_version(reply.version):
            raise ConnectionError("invalid_node_reply")
        if not reply.ok:
            raise NodeCommandError(reply.error)
        return reply.data
