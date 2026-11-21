#!/usr/bin/env python3
"""Read Linux host counters independently of the task runtime."""

import csv
from datetime import datetime, timezone
import math
import os
import re
from pathlib import Path
import subprocess
import time

INTERVAL = 5


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def cpu_counter(text):
    # Guest time is already included in user/nice; iowait counts as idle.
    values = [int(value) for value in text.splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def cpu_percent(previous, current):
    total, idle = current[0] - previous[0], current[1] - previous[1]
    return round(max(0, min(100, (total - idle) * 100 / total)), 1) if total > 0 and idle >= 0 else None


def memory_metrics(text):
    values = {line.split(":", 1)[0]: int(line.split()[1]) * 1024 for line in text.splitlines() if ":" in line}
    total, available = values["MemTotal"], values["MemAvailable"]
    used = max(0, total - available)
    return {
        "total_gib": round(total / 2**30, 2),
        "used_gib": round(used / 2**30, 2),
        "available_gib": round(available / 2**30, 2),
        "percent": round(used * 100 / total, 1),
    }


def gpu_metrics(text):
    devices = []
    for row in csv.reader(text.splitlines(), skipinitialspace=True):
        if len(row) != 10:
            raise ValueError("Unexpected NVIDIA metrics columns")
        index, uuid, name, utilization, used, total, temperature, power, limit, fan = row
        used, total = number(used), number(total)
        devices.append(
            {
                "index": int(index),
                "uuid": uuid,
                "name": name,
                "utilization_percent": number(utilization),
                "memory_used_mib": used,
                "memory_total_mib": total,
                "memory_percent": round(used * 100 / total, 1) if used is not None and total else None,
                "temperature_c": number(temperature),
                "power_w": number(power),
                "power_limit_w": number(limit),
                "fan_percent": number(fan),
            }
        )
    return devices


def npu_metrics(text):
    devices, card = [], None
    for line in text.splitlines():
        header = re.match(r"^\|\s*(\d+)\s+(\d+[A-Za-z0-9]+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s+(\S+)", line)
        if header:
            card = {"name": "Ascend " + header[2], "power_w": number(header[4]), "temperature_c": number(header[5])}
            continue
        chip = re.match(r"^\|\s*(\d+)\s+(\d+)\s*\|\s*([0-9A-Fa-f:.]+)\s*\|\s*([0-9.]+)\s+([0-9]+)\s*/\s*([0-9]+)", line)
        if chip and card:
            used, total = int(chip[5]), int(chip[6])
            devices.append({**card, "index": int(chip[2]), "uuid": f"ascend:{chip[3].lower()}:{chip[1]}",
                "utilization_percent": number(chip[4]), "memory_used_mib": used, "memory_total_mib": total,
                "memory_percent": round(used * 100 / total, 1) if total else 0,
                "power_limit_w": None, "fan_percent": None})
            card = None
    if not devices:
        raise ValueError("Unexpected Ascend metrics output")
    return sorted(devices, key=lambda device: device["index"])


def network_counters(text, net_root=Path("/sys/class/net")):
    interfaces = {}
    for line in text.splitlines()[2:]:
        name, values = line.split(":", 1)
        name, values = name.strip(), values.split()
        # Count physical uplinks, excluding loopback and container bridges/veths.
        if (net_root / name / "device").exists():
            interfaces[name] = (int(values[0]), int(values[8]))
    return interfaces


def cpu_temperatures(hwmon=Path("/sys/class/hwmon"), thermal=Path("/sys/class/thermal")):
    sensors = []
    names = {"coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal", "hisi_temp", "ampere_hwmon"}
    for root in sorted(hwmon.glob("hwmon*")):
        try:
            driver = (root / "name").read_text().strip().lower()
            if driver not in names and not any(word in driver for word in ("cpu", "kunpeng", "soc_temp")):
                continue
            for path in sorted(root.glob("temp*_input")):
                label_path = path.with_name(path.name.replace("_input", "_label"))
                label = label_path.read_text().strip() if label_path.exists() else driver
                value = float(path.read_text()) / 1000
                if 0 < value < 150:
                    sensors.append({"name": label, "temperature_c": round(value, 1)})
        except (OSError, ValueError):
            continue
    if not sensors:
        for root in sorted(thermal.glob("thermal_zone*")):
            try:
                name = (root / "type").read_text().strip()
                if any(word in name.lower() for word in ("cpu", "pkg", "soc")):
                    value = float((root / "temp").read_text()) / 1000
                    if 0 < value < 150:
                        sensors.append({"name": name, "temperature_c": round(value, 1)})
            except (OSError, ValueError):
                continue
    return {
        "temperature_c": max(sensor["temperature_c"] for sensor in sensors) if sensors else None,
        "temperature_sensors": sensors,
    }


class Sampler:
    def __init__(
        self, cfg, proc=Path("/proc"), sys=Path("/sys"), clock=time.monotonic, run=subprocess.check_output
    ):
        self.cfg = cfg
        self.proc, self.sys, self.clock, self.run = proc, sys, clock, run
        self.previous_cpu = None
        self.previous_network = None
        self.previous_time = None

    def sample(self):
        current = cpu_counter((self.proc / "stat").read_text())
        now = self.clock()
        utilization = cpu_percent(self.previous_cpu, current) if self.previous_cpu else None
        self.previous_cpu = current
        network = network_counters((self.proc / "net/dev").read_text(), self.sys / "class/net")
        elapsed = now - self.previous_time if self.previous_time is not None else None
        rates = []
        for interface, counters in network.items():
            previous = (self.previous_network or {}).get(interface)
            rates.append(
                {
                    "name": interface,
                    "rx_mib_s": round(max(0, counters[0] - previous[0]) / elapsed / 2**20, 3)
                    if previous and elapsed
                    else None,
                    "tx_mib_s": round(max(0, counters[1] - previous[1]) / elapsed / 2**20, 3)
                    if previous and elapsed
                    else None,
                }
            )
        self.previous_network, self.previous_time = network, now
        errors, devices, disks = [], [], []
        try:
            output = self.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                stderr=subprocess.PIPE,
                timeout=4,
            )
            devices = gpu_metrics(output)
        except FileNotFoundError:
            try:
                devices = npu_metrics(self.run(["npu-smi", "info"], text=True, stderr=subprocess.PIPE, timeout=4))
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                errors.append({"code": "metrics.gpu_unavailable", "parameters": {"type": type(exc).__name__}})
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            errors.append({"code": "metrics.gpu_unavailable", "parameters": {"type": type(exc).__name__}})
        for pool in self.cfg["storage"]["pools"]:
            try:
                stat = os.statvfs(pool["path"])
                total, free = stat.f_blocks * stat.f_frsize, stat.f_bfree * stat.f_frsize
                disks.append(
                    {
                        "name": pool["name"],
                        "total_gib": round(total / 2**30, 1),
                        "used_gib": round((total - free) / 2**30, 1),
                        "available_gib": round(stat.f_bavail * stat.f_frsize / 2**30, 1),
                        "percent": round((total - free) * 100 / total, 1) if total else 0,
                    }
                )
            except OSError:
                errors.append({"code": "metrics.pool_unavailable", "parameters": {"pool": pool["name"]}})
        return {
            "version": 1,
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "interval_seconds": INTERVAL,
            "cpu": {
                "percent": utilization,
                "cores": os.cpu_count(),
                "load": [round(value, 2) for value in os.getloadavg()],
                **cpu_temperatures(self.sys / "class/hwmon", self.sys / "class/thermal"),
            },
            "memory": memory_metrics((self.proc / "meminfo").read_text()),
            "gpus": devices,
            "disks": disks,
            "network": rates,
            "errors": errors,
        }
