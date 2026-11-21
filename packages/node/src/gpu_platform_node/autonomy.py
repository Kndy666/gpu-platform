from datetime import datetime

from gpu_platform_contracts.messages import ACTIVE_STATES, TaskSpec, TaskStatus
from gpu_platform_contracts.slots.runtime import Runtime


def stop_expired(runtime: Runtime, specs: list[TaskSpec], now: datetime) -> list[TaskStatus]:
    """Apply locally recorded deadlines even while the control host is unreachable."""
    if now.tzinfo is None:
        raise ValueError("expiry checks require an aware datetime")
    stopped = []
    for spec in specs:
        if spec.expires_at is not None and spec.expires_at <= now:
            try:
                current = runtime.status(spec.id)
            except (KeyError, FileNotFoundError):
                continue
            if current.state in ACTIVE_STATES:
                stopped.append(runtime.stop(spec.id))
    return stopped
