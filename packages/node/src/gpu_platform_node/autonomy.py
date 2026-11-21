from datetime import datetime
from typing import Callable

from gpu_platform_contracts.messages import TaskSpec, TaskStatus


def stop_expired(
    stop: Callable[[str], TaskStatus], specs: list[TaskSpec], now: datetime
) -> list[TaskStatus]:
    """Apply locally recorded deadlines even while the control host is unreachable."""
    if now.tzinfo is None:
        raise ValueError("expiry checks require an aware datetime")
    stopped = []
    for spec in specs:
        if spec.expires_at is not None and spec.expires_at <= now:
            stopped.append(stop(spec.id))
    return stopped
