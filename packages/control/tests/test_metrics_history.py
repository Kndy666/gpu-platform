from datetime import datetime, timezone
import os

import pytest

from gpu_platform_control.core.metrics_history import (
    DAY, HOUR, MINUTE, MIB, MetricsArchive, NoArchiveSpace, PendingMetrics, floor,
)

HOUR_START = floor(1_740_000_000, HOUR)


def sample(at, cpu, devices=None):
    return {
        "sampled_at": datetime.fromtimestamp(at, timezone.utc).isoformat(),
        "cpu": {"percent": cpu},
        "gpus": devices if devices else [],
    }


def test_archive_rolls_up_unique_samples_and_retains_gaps(tmp_path):
    archive = MetricsArchive(tmp_path, 32 * MIB, 0)
    blocks = {}
    pending = PendingMetrics()
    stamp = HOUR_START + 5
    received = sample(stamp, 0, [
        {"uuid": "device-0", "name": "Test GPU", "index": 0, "utilization_percent": 20},
        {"uuid": "device-1", "name": "Test GPU", "index": 1, "utilization_percent": 40},
    ])
    pending.add(1, received, stamp)
    pending.add(1, received, stamp)
    pending.add(1, sample(stamp + 20, 30), stamp + 20)
    assert pending.flush(archive, blocks, stamp + 90)
    assert next(iter(blocks)).read_bytes().startswith(b"\x1f\x8b")

    now = HOUR_START + HOUR + 90
    archive.maintain(blocks, now)
    archive.maintain(blocks, now)
    assert len(blocks) == 3
    current = archive.series(1, HOUR_START, HOUR_START + 120, "cpu", "", now)
    assert current["series"] == [
        [HOUR_START * 1000, 15, 0, 30, 2],
        [(HOUR_START + 60) * 1000, None, None, None, 0],
    ]
    card = archive.series(1, HOUR_START, HOUR_START + 60, "device_load", "model/Test GPU", now)
    assert card["series"][0][1:] == [30, 20, 40, 2]

    now += 8 * DAY
    archive.maintain(blocks, now)
    assert not archive.name(MINUTE, 1, HOUR_START).exists()
    old = archive.series(1, HOUR_START, HOUR_START + HOUR, "cpu", "", now)
    assert old["step"] == 900
    assert old["series"][0][1:] == [15, 0, 30, 2]
    now += 90 * DAY
    archive.maintain(blocks, now)
    assert len(blocks) == 1
    older = archive.series(1, HOUR_START, HOUR_START + HOUR, "cpu", "", now)
    assert older["step"] == HOUR and older["series"][0][1:] == [15, 0, 30, 2]
    archive.maintain(blocks, now + 365 * DAY)
    assert not blocks


@pytest.mark.parametrize("constraint", ["disk", "quota"])
def test_archive_respects_storage_limits_and_can_continue_writing(tmp_path, monkeypatch, constraint):
    archive = MetricsArchive(tmp_path, MIB, 2 * MIB)
    if constraint == "disk":
        monkeypatch.setattr(archive, "_free", lambda: 2 * MIB)
    else:
        archive.limit = 2 * os.statvfs(tmp_path).f_frsize
        monkeypatch.setattr(archive, "_free", lambda: 16 * MIB)
    pending = PendingMetrics()
    stamp = HOUR_START + 10
    pending.add(4, sample(stamp, 20), stamp)
    blocks = {}
    with pytest.raises(NoArchiveSpace):
        pending.flush(archive, blocks, stamp + 90)
    assert archive.status()["used_mib"] == 0
    assert list(tmp_path.iterdir()) == []

    stamp += 120
    pending.add(4, sample(stamp, 10), stamp)
    archive.limit = MIB
    monkeypatch.setattr(archive, "_free", lambda: 16 * MIB)
    assert pending.flush(archive, blocks, stamp + 90)
    report = archive.series(4, HOUR_START, stamp + 60, "cpu", "", stamp + 90)
    assert report["series"][0][-1] == 0
    assert report["series"][2][1:] == [10, 10, 10, 1]
