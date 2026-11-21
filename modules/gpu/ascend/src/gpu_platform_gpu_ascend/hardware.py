"""Read physical chip identity, health and memory from the driver utility."""

import re
import subprocess
from decimal import Decimal


def parse_inventory(output):
    devices = []
    card = None
    for line in output.splitlines():
        header = re.match(r"^\|\s*(\d+)\s+(\d+[A-Za-z0-9]+)\s*\|\s*(\S+)", line)
        if header:
            card = {"card": int(header[1]), "name": "Ascend " + header[2], "ready": header[3] == "OK"}
            continue
        chip = re.match(
            r"^\|\s*(\d+)\s+(\d+)\s*\|\s*([0-9A-Fa-f:.]+)\s*\|\s*([0-9.]+)\s+([0-9]+)\s*/\s*([0-9]+)", line
        )
        if chip and card:
            devices.append(
                {
                    **card,
                    "chip": int(chip[1]),
                    "index": int(chip[2]),
                    "id": f"ascend:{chip[3].lower()}:{chip[1]}",
                    "memory_gib": Decimal(chip[6]) / 1024,
                    "memory_used_mib": int(chip[5]),
                    "utilization_percent": Decimal(chip[4]),
                }
            )
            card = None
    if (
        not devices
        or len({d["id"] for d in devices}) != len(devices)
        or len({d["index"] for d in devices}) != len(devices)
    ):
        raise RuntimeError("ascend.inventory_unavailable")
    return sorted(devices, key=lambda d: d["index"])


def inventory(run=subprocess.check_output):
    return parse_inventory(run(["npu-smi", "info"], text=True, timeout=20))
