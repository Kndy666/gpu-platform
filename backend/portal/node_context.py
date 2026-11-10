"""Scope one reconciliation pass to a node without mixing execution clusters."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

active_node = ContextVar('platform_node', default=None)


@contextmanager
def using_node(node):
    token = active_node.set(node)
    try:
        yield node
    finally:
        active_node.reset(token)


def node_config(node, base):
    result = deepcopy(base)
    for section in ('platform', 'network', 'storage', 'registry'):
        result.setdefault(section, {}).update(node.runtime_config.get(section, {}))
    result['platform']['architecture'] = node.architecture
    # User-facing SSH always enters through the controller.
    result['platform']['public_host'] = base['platform']['public_host']
    return result
