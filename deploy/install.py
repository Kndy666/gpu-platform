#!/usr/bin/env python3
"""Single installation entrypoint. Discovery/render are read-only with respect to host data."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
import tarfile
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[1]

def run(argv, **kwargs):
    return subprocess.run([str(v) for v in argv], check=True, text=True, **kwargs)

def capture(argv): return run(argv, stdout=subprocess.PIPE).stdout.strip()

def discover(cfg):
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine())
    if not arch: raise RuntimeError('Only ARM64 and AMD64 are supported')
    try:
        route = json.loads(capture(['ip', '-j', '-4', 'route', 'get', '1.1.1.1']))[0]
        node_ip, interface = route['prefsrc'], route['dev']
    except (OSError, subprocess.CalledProcessError, KeyError, IndexError):
        node_ip, interface = '127.0.0.1', 'unknown'
    result = {'architecture': arch, 'cpu': os.cpu_count(), 'memory_gib': os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE') // 2**30,
              'node_ip': node_ip, 'interface': interface, 'pools': [],
              'existing_runtimes': {c: shutil.which(c) for c in ['docker', 'k3s', 'containerd', 'nvidia-container-runtime']}}
    try:
        # Inventory only: does not infer current Kubernetes GPU reservations from nvidia-smi.
        result['gpu_inventory'] = capture(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader']).splitlines()
    except (OSError, subprocess.CalledProcessError): result['gpu_inventory'] = None
    devices = set()
    paths = []
    for pool in cfg['storage']['pools']:
        path = Path(pool['path'])
        if not path.is_absolute() or '..' in path.parts or not re.fullmatch(r'[a-z][a-z0-9-]{2,62}', pool['name']):
            raise RuntimeError('Invalid pool name/path')
        if path.is_symlink(): raise RuntimeError('Pool path may not be a symlink')
        ancestor = path
        while not ancestor.exists(): ancestor = ancestor.parent
        mount = json.loads(capture(['findmnt', '-J', '-T', str(ancestor), '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS']))['filesystems'][0]
        if mount['target'] == '/' or mount['fstype'] not in {'ext4', 'xfs', 'btrfs'}:
            raise RuntimeError(f'Pool {path} must be on an existing separate data filesystem')
        if 'ro' in mount['options'].split(','): raise RuntimeError(f'Pool {path} is read-only')
        device = ancestor.stat().st_dev
        if device in devices: raise RuntimeError('Rawfile pools must be on different filesystems')
        if not 5 <= pool['buffer_percent'] <= 50: raise RuntimeError('Disk buffer must be between 5% and 50%')
        devices.add(device); paths.append(path)
        st = os.statvfs(ancestor)
        result['pools'].append({**pool, 'mount': mount, 'free_gib': st.f_bavail * st.f_frsize // 2**30,
                                'capacity_gib': st.f_blocks * st.f_frsize // 2**30, 'existing': path.exists()})
    start, end = cfg['platform']['ssh_ports']
    if not 30000 <= start <= end <= 32767: raise RuntimeError('SSH ports must be in Kubernetes NodePort range')
    return result

def locked_versions():
    return dict(line.strip().split('=', 1) for line in (ROOT/'deploy/vendor-versions.lock').read_text().splitlines() if line and not line.startswith('#'))

def dump(path, value): path.write_text(yaml.safe_dump(value, sort_keys=False))

def render(cfg, facts, out):
    cfg['platform']['architecture'] = facts['architecture']
    if Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns').exists():
        cfg['registry']['build_apparmor_profile'] = 'gpu-platform-build'
    if cfg['registry']['zot_image'].startswith('ghcr.io/project-zot/zot-linux-'):
        cfg['registry']['zot_image'] = re.sub(r'zot-linux-(arm64|amd64):', 'zot-linux-'+facts['architecture']+':', cfg['registry']['zot_image'])
    if cfg['platform'].get('node_ip') == 'auto': cfg['platform']['node_ip'] = facts['node_ip']
    if cfg['network'].get('public_interface') == 'auto': cfg['network']['public_interface'] = facts['interface']
    ipaddress.ip_address(cfg['platform']['node_ip'])
    out.mkdir(parents=True, exist_ok=True)
    dump(out/'discovery.yaml', facts)
    dump(out/'resolved-config.yaml', cfg)
    dump(out/'app-values.yaml', {'config': cfg, 'appImage': cfg['platform']['app_image'], 'https': cfg['network']['https'],
                                'worker': cfg['platform'].get('role', 'controller') == 'worker'})
    pools = {p['name']: {'path': p['path'], 'reservedCapacity': f"{p['buffer_percent']}%", 'reservedCapacityMode': 'plain'} for p in cfg['storage']['pools']}
    classes = [{'name': p['storage_class'], 'enabled': True, 'volumeBindingMode': 'WaitForFirstConsumer', 'isDefault': False,
                'allowVolumeExpansion': True, 'reclaimPolicy': 'Delete', 'fsType': 'ext4', 'storagePool': p['name'], 'thinProvision': 'false',
                'formatOptions': ['-E', 'nodiscard,assume_storage_prezeroed=1'], 'mountOptions': ['nodiscard'], 'copyOnWrite': 'false'} for p in cfg['storage']['pools']]
    dump(out/'rawfile-values.yaml', {'node': {'defaultPool': cfg['storage']['pools'][0]['name'], 'storagePools': pools},
        'storageClasses': classes, 'analytics': {'enabled': False}, 'global': {'analytics': {'enabled': False}},
        'metrics': {'enabled': True}})
    manager = {'apiVersion': 'config.kueue.x-k8s.io/v1beta1', 'kind': 'Configuration',
        'health': {'healthProbeBindAddress': ':8081'}, 'metrics': {'bindAddress': ':8443'}, 'webhook': {'port': 9443},
        'leaderElection': {'leaderElect': True, 'resourceName': 'c1f6bfd2.kueue.x-k8s.io'},
        'managedJobsNamespaceSelector': {'matchLabels': {'platform.queue': 'enabled'}}, 'integrations': {'frameworks': ['batch/job', 'pod']}}
    dump(out/'kueue-values.yaml', {'controllerManager': {'manager': {'image': {'repository': 'registry.k8s.io/kueue/kueue', 'tag': 'v'+locked_versions()['kueue'], 'pullPolicy': 'IfNotPresent'}}},
        'managerConfig': {'controllerManagerConfigYaml': yaml.safe_dump(manager)}})
    return cfg

def download(urls, dest, expected=None):
    if dest.exists() and (not expected or hashlib.sha256(dest.read_bytes()).hexdigest() == expected): return
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                temporary = dest.with_suffix('.part')
                with temporary.open('wb') as f: shutil.copyfileobj(response, f)
            if expected and hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                temporary.unlink(); raise RuntimeError('Checksum mismatch')
            temporary.replace(dest)
            return
        except Exception as e:
            print(f'Download failed from {url.split("/")[2]}: {type(e).__name__}', file=sys.stderr)
    raise RuntimeError(f'Cannot obtain {dest.name}; see offline preparation documentation')

def firewall(cfg):
    # Only restrict private control-plane ports; never flush or replace host rules.
    interface = cfg['network']['public_interface']
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]+', interface) or interface == 'unknown': raise RuntimeError('Cannot identify public interface')
    lines = ['#!/bin/sh', 'set -eu', '# Managed by GPU Resource Platform']
    for binary in ['iptables', 'ip6tables']:
        if not shutil.which(binary): raise RuntimeError(f'{binary} is required to protect control-plane ports')
        for port in cfg['network']['private_ports']:
            rule = ['INPUT', '-i', interface, '-p', 'tcp', '--dport', str(int(port)), '-m', 'comment', '--comment', 'gpu-platform-private', '-j', 'DROP']
            if subprocess.run([binary, '-C', *rule], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                run([binary, '-I', *rule])
            lines.append(f'{binary} -C INPUT -i {interface} -p tcp --dport {int(port)} -m comment --comment gpu-platform-private -j DROP 2>/dev/null || {binary} -I INPUT -i {interface} -p tcp --dport {int(port)} -m comment --comment gpu-platform-private -j DROP')
        for port in [8472]:
            rule=['INPUT','-i',interface,'-p','udp','--dport',str(port),'-m','comment','--comment','gpu-platform-private','-j','DROP']
            if subprocess.run([binary,'-C',*rule],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:run([binary,'-I',*rule])
            lines.append(f'{binary} -C INPUT -i {interface} -p udp --dport {port} -m comment --comment gpu-platform-private -j DROP 2>/dev/null || {binary} -I INPUT -i {interface} -p udp --dport {port} -m comment --comment gpu-platform-private -j DROP')
    directory = Path('/etc/rancher/k3s'); directory.mkdir(parents=True, exist_ok=True)
    guard = directory/'platform-firewall.sh'
    guard.write_text('\n'.join(lines)+'\n'); guard.chmod(0o700)

def install_k3s(cfg, facts, cache):
    versions = locked_versions()
    firewall(cfg)
    if shutil.which('k3s'):
        print('Existing K3s preserved; checking compatibility rather than reinstalling.')
        return
    arch = facts['architecture']
    asset = 'k3s-arm64' if arch == 'arm64' else 'k3s'
    ver = versions['k3s']
    urlver = ver.replace('+', '%2B')
    mirrorver = ver.replace('+', '-')
    checks = cache/'k3s-checksums.txt'
    download([f'https://rancher-mirror.rancher.cn/k3s/{mirrorver}/sha256sum-{arch}.txt',
              f'https://github.com/k3s-io/k3s/releases/download/{urlver}/sha256sum-{arch}.txt'], checks)
    sha = next(line.split()[0] for line in checks.read_text().splitlines() if line.split()[-1].lstrip('*') == asset)
    download([f'https://rancher-mirror.rancher.cn/k3s/{mirrorver}/{asset}',
              f'https://github.com/k3s-io/k3s/releases/download/{urlver}/{asset}'], cache/asset, sha)
    shutil.copy2(cache/asset, '/usr/local/bin/k3s'); os.chmod('/usr/local/bin/k3s', 0o755)
    for tool in ['kubectl', 'ctr', 'crictl']:
        target = Path('/usr/local/bin') / tool
        if not target.exists(): target.symlink_to('k3s')
    kcfg = Path('/etc/rancher/k3s')
    kcfg.mkdir(parents=True, exist_ok=True)
    if (kcfg/'config.yaml').exists(): raise RuntimeError('Refusing to overwrite an existing K3s configuration')
    if not (kcfg/'registries.yaml').exists():
        dump(kcfg/'registries.yaml', {'mirrors': {host: {'endpoint': endpoints} for host, endpoints in cfg['network'].get('registry_mirrors', {}).items()}})
    dump(kcfg/'config.yaml', {'write-kubeconfig-mode': '0600', 'node-ip': cfg['platform']['node_ip'],
        'bind-address': '0.0.0.0', 'cluster-cidr': cfg['network']['pod_cidr'], 'service-cidr': cfg['network']['service_cidr'],
        'disable': ['local-storage'] + (['traefik', 'servicelb'] if cfg['platform'].get('role') == 'worker' else []), 'kubelet-arg': [f"system-reserved=cpu={cfg['platform']['reserve_cpu']},memory={cfg['platform']['reserve_memory_gib']}Gi"],
        'secrets-encryption': True})
    manifest_dir = Path('/var/lib/rancher/k3s/server/manifests')
    manifest_dir.mkdir(parents=True, exist_ok=True)
    ingress_settings = {'apiVersion': 'helm.cattle.io/v1', 'kind': 'HelmChartConfig',
        'metadata': {'name': 'traefik', 'namespace': 'kube-system'},
        'spec': {'valuesContent': yaml.safe_dump({'ports': {'websecure': {'expose': {'default': cfg['network']['https']}}},
                                               'service': {'spec': {'allocateLoadBalancerNodePorts': False}}})}}
    dump(manifest_dir/'platform-traefik-config.yaml', ingress_settings)
    # Guard is run at every K3s startup so a reboot cannot expose the API before firewall setup.
    Path('/etc/systemd/system/k3s.service').write_text('''[Unit]
Description=Lightweight Kubernetes
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStartPre=/etc/rancher/k3s/platform-firewall.sh
ExecStart=/usr/local/bin/k3s server
KillMode=process
Delegate=yes
LimitNOFILE=1048576
LimitNPROC=infinity
TasksMax=infinity
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
''')
    run(['systemctl', 'daemon-reload']); run(['systemctl', 'enable', '--now', 'k3s'])

def storage_dependencies(cfg):
    directory = Path('/etc/systemd/system/k3s.service.d')
    directory.mkdir(parents=True, exist_ok=True)
    paths = [p['path'] for p in cfg['storage']['pools']]
    (directory/'platform-storage.conf').write_text('[Unit]\nRequiresMountsFor='+' '.join(json.dumps(p) for p in paths)+'\n')
    run(['systemctl', 'daemon-reload'])

def install_workspace_security():
    from workspace_security import install_security
    install_security({'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()])

def install_node_agent(cfg):
    agent_config = Path('/etc/rancher/k3s/platform-node-agent.yaml')
    dump(agent_config, cfg); agent_config.chmod(0o600)
    unit = Path('/etc/systemd/system/gpu-platform-node.service')
    unit.write_text('[Unit]\nDescription=GPU platform environment worker\nAfter=k3s.service\nPartOf=k3s.service\nRequires=k3s.service\n[Service]\nType=simple\nEnvironment=KUBECONFIG=/etc/rancher/k3s/k3s.yaml\nExecStart=/usr/bin/python3 '+str(ROOT/'deploy/node-agent.py')+' --config '+str(agent_config)+'\nRestart=always\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n')
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'gpu-platform-node.service'])
    run(['systemctl', 'restart', 'gpu-platform-node.service'])
    from metrics_service import install_metrics
    install_metrics(ROOT, agent_config)

def apply(cfg, facts, out):
    if os.geteuid() != 0: raise RuntimeError('Installation requires sudo')
    install_workspace_security()
    worker = cfg['platform'].get('role', 'controller') == 'worker'
    if not worker and not cfg['network'].get('public_ports_approved'): raise RuntimeError('Configure network.public_ports_approved only after administrator confirms HTTP and SSH ingress ports')
    if not worker and cfg['platform']['public_host'] == 'gpu.example.edu': raise RuntimeError('Configure public_host first')
    if facts['gpu_inventory']:
        run([ROOT/'deploy/toolkit.sh'])
    for path in [p['path'] for p in cfg['storage']['pools']] + [cfg['storage']['datasets']]:
        Path(path).mkdir(parents=True, exist_ok=True)
    cache = out/'cache'; cache.mkdir(exist_ok=True)
    install_k3s(cfg, facts, cache)
    storage_dependencies(cfg)
    env = os.environ.copy(); env['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    # K3s derives this address from bind-address on first boot; the local admin endpoint stays loopback.
    run(['k3s', 'kubectl', 'config', 'set-cluster', 'default', '--server=https://127.0.0.1:6443'], env=env)
    if cfg['network'].get('download_proxy'):
        env['HTTP_PROXY'] = env['HTTPS_PROXY'] = cfg['network']['download_proxy']
        env['NO_PROXY'] = ','.join(['127.0.0.1', 'localhost', cfg['platform']['node_ip'], '.svc', '.cluster.local', cfg['network']['pod_cidr'], cfg['network']['service_cidr']])
    deadline = time.monotonic()+180
    while time.monotonic() < deadline:
        observed = json.loads(capture(['k3s', 'kubectl', 'get', 'nodes', '-o', 'json']))
        if observed.get('items'): break
        time.sleep(2)
    else: raise RuntimeError('K3s node registration timed out')
    run(['k3s', 'kubectl', 'wait', '--for=condition=Ready', 'node', '--all', '--timeout=180s'])
    if len(observed['items']) != 1: raise RuntimeError('Exactly one Kubernetes node is supported')
    ingress_settings = {'apiVersion': 'helm.cattle.io/v1', 'kind': 'HelmChartConfig',
        'metadata': {'name': 'traefik', 'namespace': 'kube-system'},
        'spec': {'valuesContent': yaml.safe_dump({'ports': {'websecure': {'expose': {'default': cfg['network']['https']}}},
                                               'service': {'spec': {'allocateLoadBalancerNodePorts': False}}})}}
    run(['k3s', 'kubectl', 'apply', '-f', '-'], input=yaml.safe_dump(ingress_settings), env=env)
    helm = shutil.which('helm') or str(ROOT/'.tools/helm')
    if not Path(helm).exists(): run([ROOT/'deploy/prepare.sh'], env=env)
    def h(args): run([helm, *args], env=env)
    for namespace in [cfg['platform']['namespace'], cfg['platform']['workload_namespace'], cfg['platform']['build_namespace'], 'openebs', 'kueue-system']:
        obj = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace}}
        if namespace == cfg['platform']['workload_namespace']:
            obj['metadata']['labels'] = {'platform.queue': 'enabled', 'pod-security.kubernetes.io/enforce': 'baseline'}
        run(['k3s', 'kubectl', 'apply', '-f', '-'], input=yaml.safe_dump(obj))
    run([sys.executable, ROOT/'deploy/resource-view.py', '--config-stdin'], input=yaml.safe_dump(cfg))
    run([sys.executable, ROOT/'deploy/workspace-runtime.py', '--config', out/'resolved-config.yaml'])
    ver = locked_versions()
    rawfile_url = f"https://github.com/openebs/rawfile-localpv/releases/download/v{ver['rawfile_localpv']}/rawfile-localpv-{ver['rawfile_localpv']}.tgz"
    download(['https://ghfast.top/' + rawfile_url, rawfile_url], cache/'rawfile.tgz', ver['rawfile_chart_sha256'])
    h(['upgrade', '--install', 'rawfile', cache/'rawfile.tgz', '-n', 'openebs', '-f', out/'rawfile-values.yaml', '--wait', '--timeout', '10m'])
    kueue_chart = cache / f"kueue-{ver['kueue']}.tgz"
    if not kueue_chart.exists():
        h(['pull', 'oci://registry.k8s.io/kueue/charts/kueue', '--version', ver['kueue'], '--destination', cache])
    if hashlib.sha256(kueue_chart.read_bytes()).hexdigest() != ver['kueue_chart_sha256']: raise RuntimeError('Kueue chart checksum mismatch')
    h(['upgrade', '--install', 'kueue', kueue_chart, '-n', 'kueue-system', '-f', out/'kueue-values.yaml', '--timeout', '5m'])
    try:
        run(['k3s', 'kubectl', '-n', 'kueue-system', 'rollout', 'status', 'deployment/kueue-controller-manager', '--timeout=120s'], env=env)
    except subprocess.CalledProcessError:
        # v0.13.4 may wait on its initial certificate-generation race; re-read the persisted certificate once.
        run(['k3s', 'kubectl', '-n', 'kueue-system', 'rollout', 'restart', 'deployment/kueue-controller-manager'], env=env)
        run(['k3s', 'kubectl', '-n', 'kueue-system', 'rollout', 'status', 'deployment/kueue-controller-manager', '--timeout=120s'], env=env)
    install_node_agent(cfg)
    deadline = time.monotonic()+120
    while time.monotonic() < deadline:
        node = json.loads(capture(['k3s','kubectl','get','node',observed['items'][0]['metadata']['name'],'-o','json']))
        if int(node['status'].get('allocatable',{}).get('gpu.platform/compute',0)) == 100*len(facts['gpu_inventory']):
            break
        time.sleep(2)
    else:
        raise RuntimeError('Shared GPU worker has not registered the discovered GPU capacity')
    run([sys.executable, ROOT/'deploy/cluster_resources.py', '--config', out/'resolved-config.yaml'], env=env)
    if worker:
        revision = ''
    elif cfg['platform'].get('app_image_archive'):
        archive = Path(cfg['platform']['app_image_archive']).expanduser().resolve()
        if not archive.is_file(): raise RuntimeError('Application image archive does not exist')
        run(['k3s', 'ctr', 'images', 'import', '--platform', 'linux/'+facts['architecture'], archive])
        revision = hashlib.sha256(archive.read_bytes()).hexdigest()
    elif shutil.which('docker') and subprocess.run(['docker', 'image', 'inspect', cfg['platform']['app_image']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        arch = capture(['docker', 'image', 'inspect', cfg['platform']['app_image'], '--format', '{{.Architecture}}'])
        if arch != facts['architecture']: raise RuntimeError('Application image architecture mismatch')
        image_archive = cache/'platform-image.tar'
        run(['docker', 'save', '-o', image_archive, cfg['platform']['app_image']])
        run(['k3s', 'ctr', 'images', 'import', image_archive])
        image_archive.unlink()
        revision = capture(['docker', 'image', 'inspect', cfg['platform']['app_image'], '--format', '{{.Id}}'])
    else:
        revision = cfg['platform']['app_image']
    h(['upgrade', '--install', 'platform', ROOT/'deploy/helm', '-n', cfg['platform']['namespace'], '-f', out/'app-values.yaml', '--set-string', 'imageRevision='+revision, '--wait', '--timeout', '10m'])
    if not (ROOT/'.tools/nerdctl').is_file(): run([ROOT/'deploy/prepare.sh', '--nerdctl-only'], env=env)
    from node_services import install_node_services
    install_node_services(cfg, ROOT)
    if not worker:
        run(['k3s', 'kubectl', '-n', cfg['platform']['namespace'], 'exec', 'deploy/platform', '--',
             'python', 'manage.py', 'bootstrap_workspace', '--wait', '1800'], env=env)
    run(['k3s', 'kubectl', '-n', cfg['platform']['namespace'], 'rollout', 'status', 'deployment/registry', '--timeout=180s'], env=env)
    print('Worker ready. Register its SSH endpoint in the controller.' if worker else
          'Deployment completed. Initial administrator: admin. Read its initial password with sudo ./deploy/admin-password.sh')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--render-only', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT/'runtime/deploy')
    args = parser.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    if cfg['platform'].get('role', 'controller') not in {'controller', 'worker'}:
        raise RuntimeError('platform.role must be controller or worker')
    facts = discover(cfg)
    if args.preflight:
        print(yaml.safe_dump(facts, sort_keys=False)); return
    cfg = render(cfg, facts, args.output)
    if args.render_only:
        print(f'Deployment configuration rendered in {args.output}'); return
    apply(cfg, facts, args.output)

if __name__ == '__main__':
    try: main()
    except (RuntimeError, subprocess.CalledProcessError) as e:
        print(f'Installation stopped safely: {e}', file=sys.stderr); sys.exit(1)
