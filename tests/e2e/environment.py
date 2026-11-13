"""Configuration and explicit capability selection for live acceptance checks."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def enabled(feature):
    return feature in {value.strip() for value in os.environ.get('PLATFORM_TEST_FEATURES', '').split(',')}


def skip(reason):
    print(json.dumps({'status': 'skipped', 'reason': reason}), flush=True)
    raise SystemExit(0)


def require_features(*features):
    missing = [feature for feature in features if not enabled(feature)]
    if missing:
        skip('Enable PLATFORM_TEST_FEATURES=' + ','.join(features) + ' for this check')


def require_commands(*commands):
    missing = [command for command in commands if not shutil.which(command)]
    if missing:
        skip('Required commands unavailable: ' + ', '.join(missing))


def config_path():
    return Path(os.environ.get('PLATFORM_TEST_CONFIG') or os.environ.get('PLATFORM_CONFIG') or ROOT / 'config.yaml').resolve()


def deployment_config():
    import yaml
    return yaml.safe_load(config_path().read_text())


def namespace(kind='namespace'):
    return deployment_config()['platform'][kind]


def kubectl():
    configured = os.environ.get('PLATFORM_TEST_KUBECTL')
    command = shlex.split(configured) if configured else ['k3s', 'kubectl'] if shutil.which('k3s') else ['kubectl']
    require_commands(command[0])
    return command


def kube(*args, data=None):
    return subprocess.run([*kubectl(), *args], input=data, text=True,
                          stdout=subprocess.PIPE, check=True).stdout


def portal_url():
    cfg = deployment_config()
    return os.environ.get('PLATFORM_TEST_BASE') or (
        ('https://' if cfg['network']['https'] else 'http://') + cfg['platform']['public_host'])


def gpu_inventory(app, minimum=1, memory_gib=1):
    """Live sharing checks need an idle node to control concurrent allocations."""
    require_features('gpu')
    snapshot = app('''import json
from django.utils import timezone
from portal.models import SystemObservation
row = SystemObservation.objects.filter(node__transport='local', name='gpu_inventory').first()
print(json.dumps({'devices': row.data.get('devices', []) if row else [],
                  'age': (timezone.now()-row.observed_at).total_seconds() if row else None}))''')
    if snapshot['age'] is None or snapshot['age'] > 120:
        skip('GPU inventory is unavailable or stale')
    devices = [d for d in snapshot['devices'] if d.get('ready', True)]
    if len(devices) < minimum or any(d['usable_memory_mib'] < memory_gib * 1024 for d in devices):
        skip(f'Requires {minimum} ready GPUs with at least {memory_gib} GiB each')
    if any(d.get('used_compute_percent', 0) or d.get('used_memory_mib', 0) for d in devices):
        skip('GPU sharing scenarios require an idle test node')
    return devices


def quota_arguments(record, devices):
    """Compare assigned quotas to the selected physical devices before probing CUDA."""
    inventory = {device['uuid']: device for device in devices}
    allocations = sorted(record['gpu_allocations'], key=lambda item: item['slot'])
    assert len(allocations) == len(record['gpu_requests'])
    sms, memory = [], []
    for allocation, request in zip(allocations, record['gpu_requests']):
        total = inventory[allocation['uuid']]['sm_count']
        percent = request['compute_percent']
        expected_sm = total if percent == 100 else total * percent // 200 * 2
        expected_memory = request['memory_gib'] * 1024
        assert allocation['sm_count'] == expected_sm
        assert allocation['memory_mib'] == expected_memory
        sms.append(str(expected_sm))
        memory.append(str(expected_memory))
    return f"{len(allocations)} {','.join(sms)} {','.join(memory)}"
