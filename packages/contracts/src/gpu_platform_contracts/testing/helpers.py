"""Bounded polling for asynchronous slot operations, with injectable time."""

import time

from ..module import SlotPending


def await_result(
    operation, *, ready=lambda value: True, timeout=60.0, interval=0.1, clock=time.monotonic, sleep=time.sleep
):
    if timeout < 0 or interval <= 0:
        raise ValueError("Invalid polling interval or deadline")
    deadline = clock() + timeout
    while True:
        try:
            result = operation()
        except SlotPending:
            pass
        else:
            if ready(result):
                return result
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Slot operation did not become ready")
        sleep(min(interval, remaining))
