from unittest.mock import Mock

import pytest

from gpu_platform_contracts.module import ModuleContext
from gpu_platform_contracts.texts import Message
from gpu_platform_contracts.testing import NotifyContract
from gpu_platform_notify_pushplus.module import Module


@pytest.fixture
def delivery(tmp_path):
    session = Mock()
    session.post.return_value.json.return_value = {"code": 200}
    return Module(ModuleContext(node_id="control", state_directory=tmp_path), session=session), session


def test_delivery_sends_recipient_and_rendered_event(delivery):
    notifier, session = delivery
    event = Message(code="task.completed", parameters={"title": "fixture-title", "content": "fixture-body"})
    notifier.send({"token": "receiver"}, event, "en")
    sent = session.post.call_args.kwargs
    assert sent["json"]["token"] == "receiver"
    assert sent["json"]["title"] == event.parameters["title"]
    assert sent["json"]["content"] == event.parameters["content"]
    assert sent["allow_redirects"] is False
    assert sent["timeout"] == (3, 8)


def test_missing_receiver_cannot_invoke_provider(delivery):
    notifier, session = delivery
    with pytest.raises(ValueError):
        notifier.send({}, Message(code="task.completed"), "en")
    session.post.assert_not_called()


def test_provider_rejection_is_reported_to_event_worker(delivery):
    notifier, session = delivery
    session.post.return_value.json.return_value = {"code": 500}
    with pytest.raises(RuntimeError):
        notifier.send({"token": "receiver"}, Message(code="task.completed"), "en")


class TestPushPlusContract(NotifyContract):
    @pytest.fixture
    def notifier(self, delivery):
        return delivery[0]

    @pytest.fixture
    def recipient(self):
        return {"token": "contract-recipient"}

    @pytest.fixture
    def event(self):
        return Message(code="contract.event", parameters={"title": "opaque-title", "content": "opaque-content"})

    @pytest.fixture
    def language(self):
        return "en"

    @pytest.fixture
    def assert_delivery(self, delivery):
        def check(recipient, event, language):
            body = delivery[1].post.call_args.kwargs["json"]
            assert body["token"] == recipient["token"]
            assert body["title"] == event.parameters["title"]
            assert body["content"] == event.parameters["content"]
        return check
