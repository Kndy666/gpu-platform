from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from gpu_platform_contracts.handoffs import LoginAddress, PortConnection
from gpu_platform_control.loop import links as routes


class Records:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, **parameters):
        if "pk" in parameters:
            record = next(row for row in self.rows if str(row.pk) == str(parameters["pk"]))
            return SimpleNamespace(update=lambda **values: record.__dict__.update(values))
        return self

    def select_related(self, *fields):
        return [row for row in self.rows if row.enabled]


class Entry:
    def __init__(self):
        self.opens, self.closes = [], []

    def open(self, key, username, connection, public_port):
        self.opens.append(key)
        return LoginAddress(task_id=key, username=username, host=connection.host, port=public_port)

    def close(self, key):
        self.closes.append(key)


class Transport:
    def __init__(self):
        self.active = 0

    @contextmanager
    def open_port(self, endpoint, port):
        if endpoint == "unavailable":
            raise ConnectionError()
        self.active += 1
        try:
            yield PortConnection(host="127.0.0.1", port=port)
        finally:
            self.active -= 1


def row(pk, node="available"):
    return SimpleNamespace(
        pk=pk,
        enabled=True,
        node_port=30000 + pk,
        ssh_port=31000 + pk,
        request=SimpleNamespace(node=SimpleNamespace(name=node), login_user="member"),
    )


def harness(monkeypatch, rows):
    entry, transport = Entry(), Transport()
    gateway = SimpleNamespace(
        module=lambda node, slot: entry if slot == "entry" else transport, endpoint=lambda node: node.name
    )
    monkeypatch.setattr(routes, "Workspace", SimpleNamespace(objects=Records(rows)))
    return routes.Links(gateway, probe=lambda connection: True), entry, transport


def test_routes_follow_durable_port_and_lifecycle_changes(monkeypatch):
    workspace = row(1)
    links, entry, transport = harness(monkeypatch, [workspace])
    links.tick()
    links.tick()
    assert entry.opens == ["1"] and transport.active == 1
    workspace.node_port += 1
    links.tick()
    assert entry.opens == ["1", "1"] and entry.closes == ["1"]
    links.probe = lambda connection: False
    links.tick()
    assert len(entry.opens) == 3 and transport.active == 1
    workspace.enabled = False
    links.tick()
    assert not links.active and transport.active == 0
    assert workspace.connection == {}


def test_failed_route_does_not_prevent_other_nodes_reconnecting(monkeypatch):
    links, entry, transport = harness(monkeypatch, [row(1, "unavailable"), row(2)])
    with pytest.raises(ConnectionError):
        links.tick()
    assert list(links.active) == ["2"] and transport.active == 1
    links.close()
    assert transport.active == 0


def test_failed_route_replacement_clears_published_address(monkeypatch):
    workspace = row(1)
    links, _, transport = harness(monkeypatch, [workspace])
    links.tick()
    assert workspace.connection
    workspace.request.node.name = "unavailable"
    with pytest.raises(ConnectionError):
        links.tick()
    assert workspace.connection == {} and transport.active == 0
