#!/usr/bin/env python3
"""Install the approved host resource view and expose its read-only PVC."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import yaml

SERVICE = '[Service]\nExecStart=\nExecStart=/usr/bin/lxcfs -f --enable-cfs --enable-loadavg --enable-pidfd /var/lib/lxcfs\nRestart=on-failure\n'
ROOT = Path(__file__).resolve().parents[1]
RESOURCE_VIEW_ROOT = Path('/var/lib/lxcfs')
RESOURCE_VIEW_PATHS = ('proc/cpuinfo', 'proc/meminfo', 'proc/stat', 'sys/devices/system/cpu/online')


def run(args, **kwargs):
    return subprocess.check_output([str(value) for value in args], text=True, **kwargs)


def wait_for_resource_views(timeout=30):
    # A Type=simple service can be active before its FUSE mount is ready.
    deadline = time.monotonic() + timeout
    while True:
        missing = [path for path in RESOURCE_VIEW_PATHS if not (RESOURCE_VIEW_ROOT / path).is_file()]
        if not missing:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('LXCFS resource views not ready after waiting: ' + ', '.join(missing)
                               + '; check journalctl -u lxcfs')
        time.sleep(0.25)


def require_idle_views(cfg):
    raw = run(['k3s', 'kubectl', '-n', cfg['platform']['workload_namespace'], 'get', 'pods', '-o', 'json'])
    if any(volume.get('persistentVolumeClaim', {}).get('claimName') == 'resource-view'
           for pod in json.loads(raw)['items'] for volume in pod.get('spec', {}).get('volumes', [])):
        raise RuntimeError('Stop user containers before changing the LXCFS installation')


def install_lxcfs(cfg):
    metadata = json.loads((ROOT / 'deploy/patches/lxcfs-packages.json').read_text())
    try:
        version = run(['dpkg-query', '-W', '-f=${Version}', 'lxcfs'], stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        version = ''
    if version == metadata['version']:
        return
    architecture = run(['dpkg', '--print-architecture']).strip()
    record = metadata['packages'].get(architecture)
    if record is None:
        if architecture != 'amd64':
            raise RuntimeError('Unsupported LXCFS architecture: ' + architecture)
        import tempfile
        require_idle_views(cfg)
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        version_id = release['VERSION_ID'].strip('"')
        if version_id not in {'22.04', '24.04'}:
            raise RuntimeError('The LXCFS source build requires Ubuntu 22.04 or 24.04')
        with tempfile.TemporaryDirectory(prefix='gpu-platform-lxcfs-') as directory:
            run([ROOT / 'deploy/build-resource-view.sh', directory, 'docker.m.daocloud.io/library/ubuntu:' + version_id])
            package = Path(directory) / f"lxcfs_{metadata['version']}_{architecture}.deb"
            run(['apt-get', 'install', '-y', '--no-install-recommends', package],
                env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l'})
        return
    package = ROOT / 'deploy/packages' / record['file']
    if not package.is_file() or hashlib.sha256(package.read_bytes()).hexdigest() != record['sha256']:
        raise RuntimeError('Missing or invalid LXCFS CPU fix package: ' + str(package))
    require_idle_views(cfg)
    if shutil.which('lxcfs'):
        run(['systemctl', 'stop', 'lxcfs'])
    env = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l'}
    run(['apt-get', 'install', '-y', '--no-install-recommends', package], env=env)


def configure(cfg, claims_only=False, dry_run=False):
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo')
    if not claims_only and not Path('/dev/fuse').exists():
        os.mknod('/dev/fuse', stat.S_IFCHR | 0o666, os.makedev(10, 229))
    if not claims_only:
        install_lxcfs(cfg)
    dropin = Path('/etc/systemd/system/lxcfs.service.d/gpu-platform.conf')
    changed = not dropin.exists() or dropin.read_text() != SERVICE
    if changed and not claims_only:
        require_idle_views(cfg)
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(SERVICE)
    if not claims_only:
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', 'lxcfs'])
        run(['systemctl', 'restart' if changed else 'start', 'lxcfs'])
    run(['systemctl', 'is-active', 'lxcfs'])
    wait_for_resource_views()
    nodes = json.loads(run(['k3s', 'kubectl', 'get', 'nodes', '-o', 'json']))['items']
    if len(nodes) != 1:
        raise RuntimeError('A single resource-view node is required')
    hostname = nodes[0]['metadata']['labels']['kubernetes.io/hostname']
    pv = {'apiVersion': 'v1', 'kind': 'PersistentVolume', 'metadata': {'name': 'platform-resource-view'},
          'spec': {'capacity': {'storage': '1Mi'}, 'accessModes': ['ReadOnlyMany'], 'storageClassName': '',
                   'persistentVolumeReclaimPolicy': 'Retain', 'local': {'path': '/var/lib/lxcfs'},
                   'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [
                       {'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [hostname]}]}]}}}}
    claim = {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
             'metadata': {'name': 'resource-view', 'namespace': cfg['platform']['workload_namespace']},
             'spec': {'accessModes': ['ReadOnlyMany'], 'storageClassName': '', 'volumeName': 'platform-resource-view',
                      'resources': {'requests': {'storage': '1Mi'}}}}
    for obj in (pv, claim):
        run(['k3s', 'kubectl', 'apply', *(['--dry-run=server'] if dry_run else []), '-f', '-'], input=json.dumps(obj))
    print('LXCFS resource view ready.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path)
    parser.add_argument('--config-stdin', action='store_true')
    parser.add_argument('--claims-only', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    configure(yaml.safe_load(sys.stdin.read() if args.config_stdin else args.config.read_text()), args.claims_only or args.dry_run, args.dry_run)
