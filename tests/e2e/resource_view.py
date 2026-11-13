#!/usr/bin/env python3
"""Check CPU/memory files and the actual htop meters of a running Pod."""
import argparse
import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time


from environment import require_features, kubectl, namespace

def inspect_resource_view(namespace, pod, cpu, memory_gib):
    kube = [*kubectl(), '-n', namespace, 'exec']
    code = "grep -c '^processor' /proc/cpuinfo; grep -c '^cpu[0-9]' /proc/stat; awk '/^MemTotal:/ {print $2}' /proc/meminfo; cat /sys/fs/cgroup/cpu.max"
    values = subprocess.check_output([*kube, pod, '--', 'bash', '-c', code], text=True, timeout=20).splitlines()
    assert int(values[0]) == cpu, values
    assert int(values[1]) == cpu, values
    assert int(values[2]) == memory_gib * 1024**2, values
    quota, period = map(int, values[3].split())
    assert quota == cpu * period, values

    # Use a real terminal: checking htop --version cannot catch wrong CPU meters.
    master, slave = pty.openpty()
    process = None
    data = b''
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 60, 160, 0, 0))
        process = subprocess.Popen([*kube, '-it', pod, '--', 'env', 'TERM=xterm-256color',
                                    'HTOPRC=/dev/null', 'htop'], stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True)
        os.close(slave)
        slave = None
        deadline = time.monotonic() + 4
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                try:
                    data += os.read(master, 65536)
                except OSError:
                    break
        os.write(master, b'q')
        process.wait(timeout=5)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        os.close(master)
        if slave is not None:
            os.close(slave)
    ansi = r'\x1b(?:\[[0-?]*[ -/]*[@-~]|[()][0-2A-Z]|[@-_])'
    screen = re.sub(ansi, '', data.decode(errors='replace'))
    meters = sorted({int(value) for value in re.findall(r'\b(\d+)\[', screen)})
    assert meters == list(range(cpu)), {'expected': cpu, 'meters': meters, 'screen': screen[:2000]}
    return {'cpuinfo': cpu, 'proc_stat': cpu, 'htop_cpu_meters': meters, 'memory_gib': memory_gib,
            'cgroup_cpu_quota': quota / period}


if __name__ == '__main__':
    require_features('cluster')
    parser = argparse.ArgumentParser()
    parser.add_argument('pod')
    parser.add_argument('--namespace', default=namespace('workload_namespace'))
    parser.add_argument('--cpu', type=int, required=True)
    parser.add_argument('--memory-gib', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_resource_view(args.namespace, args.pod, args.cpu, args.memory_gib)))
