import pytest

from gpu_platform_contracts.module import SlotPending
from gpu_platform_contracts.testing.helpers import await_result


class Clock:
    def __init__(self):
        self.elapsed = 0

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


def test_async_operation_retries_pending_then_observes_completion():
    clock = Clock()
    states = iter([SlotPending(operation="copy"), "running", "completed"])

    def operation():
        state = next(states)
        if isinstance(state, Exception):
            raise state
        return state

    result = await_result(
        operation,
        ready=lambda state: state == "completed",
        timeout=1,
        interval=0.1,
        clock=clock.now,
        sleep=clock.sleep,
    )
    assert result == "completed" and clock.elapsed == pytest.approx(0.2)


def test_async_operation_enforces_deadline_without_swallowing_failures():
    clock = Clock()
    with pytest.raises(TimeoutError):
        await_result(
            lambda: False, ready=bool, timeout=0.25, interval=0.1, clock=clock.now, sleep=clock.sleep
        )
    assert clock.elapsed == 0.25

    def denied():
        raise PermissionError("fixture")

    with pytest.raises(PermissionError):
        await_result(denied)
