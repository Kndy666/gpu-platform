"""The node engine owns capability and status snapshots."""

from .commands import NodeEngine


def capabilities(engine: NodeEngine):
    return engine.capabilities()


def status(engine: NodeEngine):
    return engine.report()
