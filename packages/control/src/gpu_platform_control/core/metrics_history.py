"""Compressed host telemetry stored on the control state volume."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import json
import logging
import math
import os
from pathlib import Path
import re
from typing import Any

from django.conf import settings

from gpu_platform_control.slots.configuration import cluster_config

log = logging.getLogger(__name__)
MINUTE = 60
HOUR = 3600
DAY = 86400
# Resolution, file span, retention.
TIERS = {MINUTE: (HOUR, 7 * DAY), 900: (DAY, 90 * DAY), HOUR: (DAY, 365 * DAY)}
MIB = 1024**2
MAX_POINTS = 1500
MAX_SERIES = 1024
MAX_BLOCK = 32 * MIB
BLOCK_NAME = re.compile(r"(60|900|3600)-(\d+)-(\d+)\.json\.gz")
MINUTE_DELAY = 30

FIELDS = {
    "cpu": {"percent": "cpu", "temperature_c": "cpu_temperature"},
    "memory": {"percent": "memory"},
    "gpus": {"utilization_percent": "device_load", "memory_percent": "device_memory",
             "temperature_c": "device_temperature", "power_w": "device_power"},
    "disks": {"percent": "disk"},
    "network": {"rx_mib_s": "network_receive", "tx_mib_s": "network_transmit"},
}


def floor(timestamp: int, step: int) -> int:
    return timestamp // step * step


def allocated_bytes(path: Path) -> int:
    stat = path.stat()
    return stat.st_blocks * 512


def collect(series: dict, key: str, value: list) -> None:
    entry = series.get(key)
    if entry is None:
        series[key] = value.copy()
    else:
        entry[0] += value[0]
        entry[1] = min(entry[1], value[1])
        entry[2] = max(entry[2], value[2])
        entry[3] += value[3]


def measurements(data: dict):
    for group, fields in FIELDS.items():
        rows = data.get(group) or []
        if isinstance(rows, dict):
            rows = [rows] if group in {"cpu", "memory"} else []
        for row in rows:
            identity = row.get("uuid") or row.get("name")
            if group not in {"cpu", "memory"} and not identity:
                continue
            label = row.get("name", "")
            if group == "gpus":
                label = f'{label} · {row.get("index", identity)}'
            for field, metric in fields.items():
                value = row.get(field)
                if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                    info = {"label": label}
                    if group == "gpus" and row.get("name"):
                        info["model"] = row["name"]
                    yield f"{metric}/{identity or ''}", info, value


@dataclass(frozen=True)
class Block:
    path: Path
    step: int
    node: int
    start: int
    size: int

    @property
    def end(self):
        return self.start + TIERS[self.step][0]


class NoArchiveSpace(OSError):
    pass


class MetricsArchive:
    def __init__(self, root: Path, limit: int, reserve: int):
        self.root, self.limit, self.reserve = root, limit, reserve

    @classmethod
    def configured(cls):
        options = cluster_config().control.metrics_history
        return cls(settings.DATA_DIR / "metrics-history", options.max_mib * MIB, options.reserve_mib * MIB)

    def blocks(self):
        for path in self.root.glob("*.json.gz"):
            match = BLOCK_NAME.fullmatch(path.name)
            if match:
                try:
                    step, node, start = map(int, match.groups())
                    yield Block(path, step, node, start, allocated_bytes(path))
                except FileNotFoundError:
                    continue

    def name(self, step: int, node: int, time: int):
        return self.root / f"{step}-{node}-{floor(time, TIERS[step][0])}.json.gz"

    @staticmethod
    def read(path: Path) -> dict:
        try:
            with gzip.open(path, "rb") as stream:
                contents = stream.read(MAX_BLOCK + 1)
            if len(contents) > MAX_BLOCK:
                raise ValueError("Metrics archive block is too large")
            data = json.loads(contents)
            if data.get("version") != 1 or not isinstance(data.get("points"), dict) or not isinstance(data.get("labels"), dict):
                raise ValueError("Invalid metrics archive block")
            return data
        except FileNotFoundError:
            return {"labels": {}, "points": {}}
        except (OSError, EOFError, ValueError, AttributeError):
            log.warning("Unreadable metrics block: %s", path.name)
            return {"labels": {}, "points": {}}

    def _free(self):
        stat = os.statvfs(self.root if self.root.exists() else self.root.parent)
        return stat.f_bavail * stat.f_frsize

    def _delete(self, block: Block, blocks: dict) -> None:
        block.path.unlink(missing_ok=True)
        blocks.pop(block.path, None)

    def _room(self, blocks: dict, extra: int, writing: Path):
        used = sum(block.size for block in blocks.values())
        status = self.root / "status.json"
        if status.exists():
            used += allocated_bytes(status)
        free = self._free()
        if used + extra <= self.limit and free - extra >= self.reserve:
            return
        covered: dict[Path, dict] = {}
        for block in sorted(blocks.values(), key=lambda b: b.start):
            if block.step != MINUTE or block.path == writing:
                continue
            hourly = self.name(HOUR, block.node, block.start)
            if hourly not in covered:
                covered[hourly] = self.read(hourly)["points"]
            if str(block.start) in covered[hourly]:
                self._delete(block, blocks)
                used -= block.size
                free = self._free()
                if used + extra <= self.limit and free - extra >= self.reserve:
                    return
        raise NoArchiveSpace("Metrics history storage limit or free-space reserve reached")

    def _write(self, path: Path, contents: bytes, blocks: dict):
        stat = os.statvfs(self.root)
        extra = math.ceil(len(contents) / stat.f_frsize) * stat.f_frsize
        self._room(blocks, extra + (2 * stat.f_frsize if path.suffix == ".gz" else 0), path)
        temporary = self.root / ".metrics-write.tmp"
        try:
            with temporary.open("wb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, step: int, node: int, time: int, data: dict, blocks: dict):
        path = self.name(step, node, time)
        plain = json.dumps({"version": 1, **data}, separators=(",", ":"), allow_nan=False).encode()
        if len(plain) > MAX_BLOCK:
            raise ValueError("Metrics archive block is too large")
        self._write(path, gzip.compress(plain, compresslevel=3, mtime=0), blocks)
        blocks[path] = Block(path, step, node, floor(time, TIERS[step][0]), allocated_bytes(path))

    def maintain(self, blocks: dict, now: int):
        for block in list(blocks.values()):
            if block.step != MINUTE and block.end <= now - TIERS[block.step][1]:
                self._delete(block, blocks)

        # Each hourly source replaces its previous aggregate, including on a retry.
        cached: dict[Path, dict] = {}
        for block in sorted(blocks.values(), key=lambda block: block.start):
            if block.step != MINUTE or block.end + MINUTE_DELAY > now:
                continue
            if block.end <= now - TIERS[900][1]:
                continue
            outputs = {}
            for step in (900, HOUR):
                path = self.name(step, block.node, block.start)
                if path not in cached:
                    cached[path] = self.read(path)
                target = cached[path]
                if str(block.start) not in target.get("hours", []):
                    outputs[step] = target
            if outputs:
                fine = self.read(block.path)
                if fine["points"]:
                    for step, target in outputs.items():
                        for stamp, values in fine["points"].items():
                            bucket = target["points"].setdefault(str(floor(int(stamp), step)), {})
                            for key, entry in values.items():
                                collect(bucket, key, entry)
                        target["labels"].update(fine["labels"])
                        target.setdefault("hours", []).append(str(block.start))
                        self.save(step, block.node, block.start, target, blocks)
        for block in list(blocks.values()):
            if block.step == MINUTE and block.end <= now - TIERS[MINUTE][1]:
                self._delete(block, blocks)

    def write_status(self, state: str, last_write: int | None, blocks: dict):
        self._write(self.root / "status.json", json.dumps({"state": state, "last_write": last_write}).encode(), blocks)

    def status(self):
        try:
            data = json.loads((self.root / "status.json").read_text())
        except (OSError, ValueError):
            data = {"state": "waiting", "last_write": None}
        try:
            blocks = list(self.blocks())
            used = sum(block.size for block in blocks)
            first = None
            for block in sorted(blocks, key=lambda block: block.start):
                if first is not None and block.start >= first:
                    break
                points = self.read(block.path)["points"]
                if points:
                    stamp = min(map(int, points))
                    first = stamp if first is None else min(stamp, first)
            if self._free() <= self.reserve:
                data["state"] = "space"
        except OSError:
            data["state"] = "error"
            used, first = 0, None
        return {**data, "used_mib": round(used / MIB, 2), "max_mib": self.limit // MIB, "first": first}

    def _windows(self, node, start, end, cutoffs):
        grouped: dict[int, dict] = {}
        for source, (span, _) in TIERS.items():
            lower = max(start, cutoffs[source])
            upper = min(end, cutoffs[MINUTE if source == 900 else 900]) if source != MINUTE else end
            if lower >= upper:
                continue
            for block in range(floor(lower, span), upper, span):
                data = self.read(self.name(source, node, block))
                window_start, window_end = max(lower, block), min(upper, block + span)
                if source == MINUTE and not data["points"]:
                    # Disk-pressure cleanup can leave a coarser copy of a recent hour.
                    day = floor(block, DAY)
                    if day not in grouped:
                        grouped[day] = self.read(self.name(900, node, day))
                    if str(block) in grouped[day].get("hours", []):
                        yield 900, window_start, window_end, grouped[day]
                        continue
                yield source, window_start, window_end, data

    def series(self, node: int, start: int, end: int, metric: str, selected: str, now: int):
        cutoffs = {step: floor(now - retention, span) for step, (span, retention) in TIERS.items()}
        start = max(start, cutoffs[HOUR])
        source_step = MINUTE if start >= cutoffs[MINUTE] else 900 if start >= cutoffs[900] else HOUR
        if source_step == MINUTE and end - start <= MAX_POINTS * MINUTE:
            for hour in range(floor(start, HOUR), end, HOUR):
                if not self.name(MINUTE, node, hour).exists() and str(hour) in self.read(
                    self.name(900, node, hour)
                ).get("hours", []):
                    source_step = 900
                    break
        step = next(size for size in (MINUTE, 900, HOUR, 4 * HOUR, DAY)
                    if size >= source_step and math.ceil((end - start) / size) + 1 <= MAX_POINTS)
        points: dict[int, dict] = defaultdict(dict)
        labels: dict[str, dict] = {}
        for source, lower, upper, data in self._windows(node, start, end, cutoffs):
            for stamp, values in data["points"].items():
                stamp = int(stamp)
                if stamp >= upper or stamp + source <= lower:
                    continue
                bucket = points[floor(stamp, step)]
                for key, value in values.items():
                    if not key.startswith(metric + "/"):
                        continue
                    info = data["labels"][key]
                    labels[key] = info
                    collect(bucket, key, value)
                    if info.get("model"):
                        model_key = "model/" + info["model"]
                        labels[model_key] = {"label": info["model"], "group": True}
                        collect(bucket, model_key, value)
        key = selected if selected in labels else next(iter(sorted(labels, key=lambda key: (not labels[key].get("group"), key))), "")
        values = []
        for stamp in range(floor(start, step), end, step):
            value = points.get(stamp, {}).get(key)
            values.append([stamp * 1000, round(value[0] / value[3], 3), value[1], value[2], value[3]]
                          if value else [stamp * 1000, None, None, None, 0])
        return {"series": values, "step": step, "choices": labels, "selected": key,
                "has_data": any(value[4] for value in values)}


class PendingMetrics:
    """Aggregate a bounded window of incoming samples before the minute is sealed."""

    def __init__(self):
        self.buckets: dict[tuple[int, int], dict[str, Any]] = {}

    def add(self, node: int, data: dict, now: int):
        sampled = datetime.fromisoformat(data["sampled_at"])
        if sampled.tzinfo is None:
            return
        seconds = sampled.astimezone(timezone.utc).timestamp()
        minute = floor(int(seconds), MINUTE)
        if minute + MINUTE + MINUTE_DELAY <= now or seconds > now + 5:
            return
        block = self.buckets.setdefault((node, minute), {"seen": set(), "values": {}, "labels": {}})
        timestamp = seconds
        if timestamp in block["seen"] or len(block["seen"]) >= MINUTE:
            return
        block["seen"].add(timestamp)
        for key, label, value in measurements(data):
            if key in block["values"] or len(block["values"]) < MAX_SERIES:
                collect(block["values"], key, [value, value, value, 1])
                block["labels"][key] = label

    def flush(self, archive: MetricsArchive, blocks: dict, now: int) -> bool:
        ready = [key for key in self.buckets if key[1] + MINUTE + MINUTE_DELAY <= now]
        hours: dict[tuple[int, int], list] = defaultdict(list)
        for node, minute in ready:
            bucket = self.buckets.pop((node, minute))
            if bucket["values"]:
                hours[node, floor(minute, HOUR)].append((minute, bucket))
        written = False
        for (node, hour), minutes in hours.items():
            data = archive.read(archive.name(MINUTE, node, hour))
            for minute, bucket in minutes:
                if str(minute) not in data["points"]:
                    data["labels"].update(bucket["labels"])
                    data["points"][str(minute)] = bucket["values"]
            archive.save(MINUTE, node, hour, data, blocks)
            written = True
        return written
