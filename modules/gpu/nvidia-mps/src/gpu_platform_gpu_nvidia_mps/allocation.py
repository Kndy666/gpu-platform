"""GPU partition allocation and quota arithmetic."""

from itertools import permutations
from decimal import Decimal, InvalidOperation

COMPUTE = "gpu.platform/compute"
MEMORY = "gpu.platform/memory-mib"
GATE = "gpu.platform/allocation"
MIB_IN_GIB = Decimal(1) / 1024


def sm_count(total, percent):
    return int(total) if percent == 100 else int(total) * int(percent) // 200 * 2


def memory_mib(value):
    try:
        amount = Decimal(str(value)) * 1024
    except InvalidOperation as exc:
        raise ValueError("GPU memory must be specified in whole MiB.") from exc
    if not amount.is_finite() or amount < 1 or amount != amount.to_integral_value():
        raise ValueError("GPU memory must be specified in whole MiB.")
    return int(amount)


def validate_gpu_requests(requests, count, allow_max=False):
    if not isinstance(requests, list) or len(requests) != count:
        raise ValueError("Specify compute and memory for each GPU.")
    for index, request in enumerate(requests, 1):
        if not isinstance(request, dict) or set(request) != {"compute_percent", "memory_gib"}:
            raise ValueError(f"Invalid allocation for GPU {index}.")
        if not (allow_max and request["compute_percent"] == "max") and (
            type(request["compute_percent"]) is not int or not 2 <= request["compute_percent"] <= 100
        ):
            raise ValueError(f"GPU {index} compute must be between 2 and 100 percent.")
        if not (allow_max and request["memory_gib"] == "max"):
            memory_mib(request["memory_gib"])


def has_gpu_max(requests):
    return isinstance(requests, list) and any(isinstance(r, dict) and "max" in r.values() for r in requests)


def _share_maximum(values, caps, total, minimum):
    if any(v != "max" and (v < minimum or v > cap) for v, cap in zip(values, caps)):
        return None
    remaining = (sum(caps) if total is None else total) - sum(v for v in values if v != "max")
    selected = [i for i, v in enumerate(values) if v == "max"]
    if remaining < len(selected) * minimum or any(caps[i] < minimum for i in selected):
        return None
    result = [0 if v == "max" else v for v in values]
    pending = selected[:]
    while remaining > 0 and pending:
        share = max(1, remaining // len(pending))
        for i in pending:
            amount = min(share, caps[i] - result[i], remaining)
            result[i] += amount
            remaining -= amount
        pending = [i for i in pending if result[i] < caps[i]]
    return result if all(v >= minimum for v in result) else None


def resolve_gpu_max(requests, limits):
    """Resolve maximum shares in percentage points and MiB before reserving resources."""
    validate_gpu_requests(requests, len(requests), allow_max=True)
    if not has_gpu_max(requests):
        return [dict(r) for r in requests]
    devices = limits["devices"]
    if len(devices) < len(requests):
        raise ValueError("GPU inventory is incomplete.")
    compute_requests = [request["compute_percent"] for request in requests]
    compute_caps = [limits["compute_per_card_percent"]] * len(requests)
    memory_requests = [
        "max" if request["memory_gib"] == "max" else memory_mib(request["memory_gib"]) for request in requests
    ]
    memory_caps = [
        int(Decimal(str(min(device["memory_gib"], limits["memory_per_card_gib"]))) * 1024) for device in devices
    ]
    memory_limit = limits["memory_total_gib"]
    if memory_limit is not None:
        memory_limit = int(Decimal(str(memory_limit)) * 1024)
    best = None
    best_score = None
    for indices in permutations(range(len(devices)), len(requests)):
        group = [devices[i] for i in indices]
        compute = _share_maximum(compute_requests, compute_caps, limits["compute_total_percent"], 2)
        memory = _share_maximum(memory_requests, [memory_caps[i] for i in indices], memory_limit, 1)
        if compute is None or memory is None:
            continue
        if not all(sm_count(d["sm_count"], p) > 0 for d, p in zip(group, compute)):
            continue
        score = (-sum(compute), -sum(memory), indices)
        if best_score is None or score < best_score:
            best_score = score
            best = [{"compute_percent": p, "memory_gib": m * MIB_IN_GIB} for p, m in zip(compute, memory)]
    if best is None:
        raise ValueError("Requested per-device quotas cannot be allocated.")
    return best


def choose_devices(devices, leases, requests):
    """Reserve distinct physical GPUs, with both quotas fitting on every GPU."""
    usage = {d["uuid"]: [0, 0, 0] for d in devices}
    for lease in leases:
        for a in lease:
            if a["uuid"] in usage:
                value = usage[a["uuid"]]
                value[0] += a["compute_percent"]
                value[1] += a["memory_mib"]
                value[2] += a["sm_count"]
    best = None
    best_score = None
    for group in permutations([d for d in devices if d.get("ready", True)], len(requests)):
        allocations = []
        remaining_compute = 0
        remaining_memory = 0
        for slot, (device, request) in enumerate(zip(group, requests)):
            percent = request["compute_percent"]
            requested_mib = memory_mib(request["memory_gib"])
            used = usage[device["uuid"]]
            sms = sm_count(device["sm_count"], percent)
            if (
                not sms
                or used[0] + percent > 100
                or used[1] + requested_mib > device["usable_memory_mib"]
                or used[2] + sms > device["sm_count"]
            ):
                break
            remaining_compute += 100 - used[0] - percent
            remaining_memory += device["usable_memory_mib"] - used[1] - requested_mib
            allocations.append(
                {
                    "slot": slot,
                    "uuid": device["uuid"],
                    "index": device["index"],
                    "sm_count": sms,
                    "compute_percent": percent,
                    "actual_compute_percent": round(sms / device["sm_count"] * 100, 2),
                    "memory_mib": requested_mib,
                }
            )
        else:
            score = (remaining_compute, remaining_memory, tuple(d["index"] for d in group))
            if best_score is None or score < best_score:
                best = allocations
                best_score = score
    return best
