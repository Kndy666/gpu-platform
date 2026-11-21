import pytest

from gpu_platform_contracts.module import Module, ModuleContext, ModuleDescriptor
from gpu_platform_contracts.texts import LocalizedText
from gpu_platform_node.events import Events


def test_module_events_survive_restart_until_explicit_acknowledgement(tmp_path):
    class RuntimeEventSource(Module):
        descriptor = ModuleDescriptor(
            name="event-source",
            slot="runtime",
            version="1",
            side="node",
            events={"runtime.fixture": LocalizedText(en="Event", zh_hans="事件")},
        )

    journal = Events(tmp_path)
    context = ModuleContext()
    context.bind_events(journal.publish)
    emitted = RuntimeEventSource(context).emit("runtime.fixture", task_id="task", count=3)
    journal.publish(emitted)
    restarted = Events(tmp_path)
    assert restarted.pending() == [emitted]
    with pytest.raises(ValueError):
        RuntimeEventSource(context).emit("unregistered")
    assert restarted.pending()[0].parameters == {"count": 3}
    restarted.acknowledge([str(emitted.id)])
    restarted.acknowledge([str(emitted.id)])
    assert restarted.pending() == []
    assert "_event_sink" not in ModuleContext.model_json_schema()["properties"]
