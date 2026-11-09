#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import re
import subprocess
from portal_lifecycle import Acceptance, kube

def main():
    p = argparse.ArgumentParser(); p.add_argument('--base', required=True); p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--samples', type=int, default=5); args = p.parse_args()
    test = Acceptance(args.base, args.directory); test.prepare()
    inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader'], text=True)
    devices = {int(line.split(',')[0]): line.split(',')[1].strip() for line in inventory.strip().splitlines()}
    topology = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', subprocess.check_output(['nvidia-smi', 'topo', '-m'], text=True))
    rows = {}
    for line in topology.splitlines():
        words = line.split()
        if words and re.fullmatch(r'GPU\d+', words[0]): rows[int(words[0][3:])] = words[1:1+len(devices)]
    distance = {'PIX': 1, 'PXB': 2, 'PHB': 3, 'NODE': 4, 'SYS': 5}
    def cost(a, b):
        label = rows[a][b]
        return 0 if label.startswith('NV') else distance.get(label, 9)
    report = {'policy': 'NVIDIA Device Plugin GetPreferredAllocation best effort', 'samples': []}
    for n in range(args.samples):
        task = test.create(test.users[1], '验收 · 双卡自动组合 '+str(n), gpu=2)
        test.wait(task, {'running'})
        try:
            chosen = test.ssh(task, 'nvidia-smi --query-gpu=uuid --format=csv,noheader').stdout.strip().splitlines()
            assert len(chosen) == 2 and len(set(chosen)) == 2
            indexes = [next(i for i, value in devices.items() if value == gpu) for gpu in chosen]
            value = {'uuids': chosen, 'pair_distance': cost(*indexes), 'host_best_distance': min(cost(a, b) for a in devices for b in devices if a < b)}
            value['matches_host_best'] = value['pair_distance'] == value['host_best_distance']
            report['samples'].append(value)
            print('two-GPU sample', n, 'pair score', value['pair_distance'], 'best', value['host_best_distance'], flush=True)
        finally:
            test.stop(task)
    report['status'] = 'observed-preferred' if all(x['matches_host_best'] for x in report['samples']) else 'best-effort-limitation'
    args.directory.joinpath('gpu-pairs.json').write_text(json.dumps(report, indent=2))
    code = 'import json,sys; from portal.models import SystemObservation; SystemObservation.objects.update_or_create(name="gpu_affinity",defaults={"data":json.load(sys.stdin)})'
    kube('-n', 'gpu-platform', 'exec', '-i', 'deploy/platform', '--', 'python', 'manage.py', 'shell', '-c', code, data=json.dumps(report))
    test.passed('actual_two_gpu_preferred_allocation_observation', status=report['status'], samples=len(report['samples']))

if __name__ == '__main__': main()
