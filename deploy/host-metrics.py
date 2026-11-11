#!/usr/bin/env python3
"""Read host counters and publish one bounded node annotation every five seconds."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import time

import yaml

ANNOTATION = 'platform.performance'
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
    values = {line.split(':', 1)[0]: int(line.split()[1]) * 1024 for line in text.splitlines() if ':' in line}
    total, available = values['MemTotal'], values['MemAvailable']
    used = max(0, total - available)
    return {'total_gib': round(total / 2**30, 2), 'used_gib': round(used / 2**30, 2),
            'available_gib': round(available / 2**30, 2), 'percent': round(used * 100 / total, 1)}


def gpu_metrics(text):
    devices = []
    for row in csv.reader(text.splitlines(), skipinitialspace=True):
        if len(row) != 10:
            raise ValueError('Unexpected NVIDIA metrics columns')
        index, uuid, name, utilization, used, total, temperature, power, limit, fan = row
        used, total = number(used), number(total)
        devices.append({'index': int(index), 'uuid': uuid, 'name': name,
            'utilization_percent': number(utilization), 'memory_used_mib': used, 'memory_total_mib': total,
            'memory_percent': round(used * 100 / total, 1) if used is not None and total else None,
            'temperature_c': number(temperature), 'power_w': number(power), 'power_limit_w': number(limit),
            'fan_percent': number(fan)})
    return devices


def network_counters(text):
    interfaces = {}
    for line in text.splitlines()[2:]:
        name, values = line.split(':', 1)
        name, values = name.strip(), values.split()
        # Count physical uplinks, excluding loopback and container bridges/veths.
        if (Path('/sys/class/net') / name / 'device').exists():
            interfaces[name] = (int(values[0]), int(values[8]))
    return interfaces


def cpu_temperatures(hwmon=Path('/sys/class/hwmon'), thermal=Path('/sys/class/thermal')):
    sensors = []
    names = {'coretemp', 'k10temp', 'zenpower', 'cpu_thermal', 'soc_thermal', 'hisi_temp', 'ampere_hwmon'}
    for root in sorted(hwmon.glob('hwmon*')):
        try:
            driver = (root / 'name').read_text().strip().lower()
            if driver not in names and not any(word in driver for word in ('cpu', 'kunpeng', 'soc_temp')):
                continue
            for path in sorted(root.glob('temp*_input')):
                label_path = path.with_name(path.name.replace('_input', '_label'))
                label = label_path.read_text().strip() if label_path.exists() else driver
                value = float(path.read_text()) / 1000
                if 0 < value < 150:
                    sensors.append({'name': label, 'temperature_c': round(value, 1)})
        except (OSError, ValueError):
            continue
    if not sensors:
        for root in sorted(thermal.glob('thermal_zone*')):
            try:
                name = (root / 'type').read_text().strip()
                if any(word in name.lower() for word in ('cpu', 'pkg', 'soc')):
                    value = float((root / 'temp').read_text()) / 1000
                    if 0 < value < 150:
                        sensors.append({'name': name, 'temperature_c': round(value, 1)})
            except (OSError, ValueError):
                continue
    return {'temperature_c': max(sensor['temperature_c'] for sensor in sensors) if sensors else None,
            'temperature_sensors': sensors}


class Sampler:
    def __init__(self, cfg):
        self.cfg = cfg
        self.previous_cpu = None
        self.previous_network = None
        self.previous_time = None

    def sample(self):
        current = cpu_counter(Path('/proc/stat').read_text())
        now = time.monotonic()
        utilization = cpu_percent(self.previous_cpu, current) if self.previous_cpu else None
        self.previous_cpu = current
        network = network_counters(Path('/proc/net/dev').read_text())
        elapsed = now - self.previous_time if self.previous_time is not None else None
        rates = []
        for interface, counters in network.items():
            previous = (self.previous_network or {}).get(interface)
            rates.append({'name': interface,
                'rx_mib_s': round(max(0, counters[0] - previous[0]) / elapsed / 2**20, 3) if previous and elapsed else None,
                'tx_mib_s': round(max(0, counters[1] - previous[1]) / elapsed / 2**20, 3) if previous and elapsed else None})
        self.previous_network, self.previous_time = network, now
        errors, devices, disks = [], [], []
        try:
            output = subprocess.check_output(['nvidia-smi',
                '--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed',
                '--format=csv,noheader,nounits'], text=True, stderr=subprocess.PIPE, timeout=4)
            devices = gpu_metrics(output)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            errors.append('GPU 采样暂不可用：' + type(exc).__name__)
        for pool in self.cfg['storage']['pools']:
            try:
                stat = os.statvfs(pool['path'])
                total, free = stat.f_blocks * stat.f_frsize, stat.f_bfree * stat.f_frsize
                disks.append({'name': pool['name'], 'total_gib': round(total / 2**30, 1),
                    'used_gib': round((total - free) / 2**30, 1), 'available_gib': round(stat.f_bavail * stat.f_frsize / 2**30, 1),
                    'percent': round((total - free) * 100 / total, 1) if total else 0})
            except OSError:
                errors.append('存储池 ' + pool['name'] + ' 暂不可用')
        return {'version': 1, 'sampled_at': datetime.now(timezone.utc).isoformat(), 'interval_seconds': INTERVAL,
            'cpu': {'percent': utilization, 'cores': os.cpu_count(), 'load': [round(value, 2) for value in os.getloadavg()],
                    **cpu_temperatures()},
            'memory': memory_metrics(Path('/proc/meminfo').read_text()), 'gpus': devices, 'disks': disks,
            'network': rates, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    sampler = Sampler(cfg)
    node = None
    while True:
        started = time.monotonic()
        try:
            if node is None:
                nodes = json.loads(subprocess.check_output(['k3s', 'kubectl', '--cache-dir=/tmp/gpu-platform-kube-cache', 'get', 'nodes', '-o', 'json'], text=True, timeout=5))['items']
                if len(nodes) != 1:
                    raise RuntimeError('Expected one node in the local cluster')
                node = nodes[0]['metadata']['name']
            data = sampler.sample()
            subprocess.run(['k3s', 'kubectl', '--cache-dir=/tmp/gpu-platform-kube-cache', 'annotate', 'node', node,
                ANNOTATION + '=' + json.dumps(data, ensure_ascii=False, separators=(',', ':')), '--overwrite'],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
        except Exception as exc:
            print('Performance sampling: ' + type(exc).__name__ + ': ' + str(exc)[:160], flush=True)
        time.sleep(max(.1, INTERVAL - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
