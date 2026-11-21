"""Hold login routes while reconciling their durable workspace records."""

import socket
from contextlib import ExitStack
from dataclasses import dataclass

from gpu_platform_contracts.handoffs import PortConnection
from gpu_platform_contracts.slots.entry import Entry
from gpu_platform_control.core.tasks.models import Workspace


@dataclass
class Route:
    module: Entry
    stack: ExitStack
    signature: tuple
    connection: PortConnection


def listening(connection):
    try:
        with socket.create_connection((connection.host, connection.port), timeout=0.3):
            return True
    except OSError:
        return False


class Links:
    def __init__(self, gateway, probe=listening):
        self.gateway = gateway
        self.probe = probe
        self.active: dict[str, Route] = {}

    def remove(self, key):
        route = self.active.pop(key)
        try:
            route.module.close(key)
        finally:
            route.stack.close()
            Workspace.objects.filter(pk=key).update(connection={})

    def tick(self):
        workspaces = list(
            Workspace.objects.filter(
                request__status="running", cleanup_complete=False, node_port__isnull=False
            ).select_related("request__node")
        )
        wanted = {str(row.pk) for row in workspaces}
        for key in set(self.active) - wanted:
            self.remove(key)
        failures = []
        for workspace in workspaces:
            key, node = str(workspace.pk), workspace.request.node
            signature = (node.name, workspace.node_port, workspace.ssh_port, workspace.request.login_user)
            current = self.active.get(key)
            if current and current.signature == signature and self.probe(current.connection):
                continue
            if current:
                self.remove(key)
            stack = ExitStack()
            entry = None
            try:
                transport = self.gateway.module(node, "transport")
                connection = stack.enter_context(
                    transport.open_port(self.gateway.endpoint(node), workspace.node_port)
                )
                entry = self.gateway.module(node, "entry")
                address = entry.open(key, workspace.request.login_user, connection, workspace.ssh_port)
                Workspace.objects.filter(pk=workspace.pk).update(connection=address.model_dump(mode="json"))
                self.active[key] = Route(entry, stack, signature, connection)
            except Exception as exc:
                Workspace.objects.filter(pk=workspace.pk).update(connection={})
                try:
                    if entry is not None:
                        entry.close(key)
                finally:
                    stack.close()
                failures.append(exc)
        if failures:
            raise failures[0]

    def close(self):
        for key in list(self.active):
            self.remove(key)
