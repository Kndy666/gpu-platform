#!/usr/bin/env python3
"""Exercise the published default image in a disposable K3s Pod over SSH."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import uuid
import yaml
from resource_view import inspect_resource_view


def run(args, **kwargs):
    return subprocess.check_output([str(v) for v in args], text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resource-view-only', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo')
    root = Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load((root / 'config.yaml').read_text())
    namespace = cfg['platform']['workload_namespace']
    name = 'tools-check-' + uuid.uuid4().hex[:12]
    kube = ['k3s', 'kubectl', '-n', namespace]
    code = "import json; from portal.models import ImageArtifact; i=ImageArtifact.objects.filter(shared=True,verified=True).latest('created_at'); print(json.dumps({'image':i.reference.split('@')[0]+'@'+i.digest}))"
    raw = run(['k3s', 'kubectl', '-n', cfg['platform']['namespace'], 'exec', 'deployment/platform', '--', 'python', 'manage.py', 'shell', '-c', code])
    image = json.loads(raw.strip().splitlines()[-1])['image']
    labels = {'platform.tools-check': name, 'platform.task': str(uuid.uuid4()), 'platform.user': '1',
              'kueue.x-k8s.io/queue-name': 'user-1'}
    with tempfile.TemporaryDirectory(prefix='gpu-platform-tools-check-') as directory:
        directory = Path(directory)
        key = directory / 'identity'
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key])
        secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name}, 'stringData': {
            'authorized_keys': key.with_suffix('.pub').read_text(), 'task.env': 'TASK_MODE=interactive\nTASK_DIRECTORY=/workspace\nTASK_USER=admin\nTASK_HOME=/home/admin\nTASK_CPU=2\nTASK_ENV_NAMES=\n',
            'runner.sh': (root / 'images/runner.sh').read_text()}}
        security = {'allowPrivilegeEscalation': True,
                    'appArmorProfile': {'type': 'Localhost', 'localhostProfile': 'gpu-platform-workspace'}}
        pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'labels': labels}, 'spec': {
            'restartPolicy': 'Never', 'runtimeClassName': cfg['platform']['runtime_classes']['cpu'],
            'automountServiceAccountToken': False, 'serviceAccountName': 'workspace', 'enableServiceLinks': False,
            'schedulingGates': [{'name': 'kueue.x-k8s.io/admission'}],
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000, 'fsGroup': 1000,
                                'seccompProfile': {'type': 'Localhost', 'localhostProfile': 'gpu-platform-workspace.json'}},
            'imagePullSecrets': [{'name': 'registry-u1'}],
            'containers': [{'name': 'workspace', 'image': image, 'securityContext': security,
                            'command': ['/bin/bash', '/run/platform/runner.sh', '/run/platform/task.env'],
                            'env': [{'name':'HOME','value':'/home/admin'}],
                            'resources': {'requests': {'cpu': '2', 'memory': '2Gi'}, 'limits': {'cpu': '2', 'memory': '2Gi'}},
                            'readinessProbe': {'tcpSocket': {'port': 2222}, 'periodSeconds': 2},
                            'volumeMounts': [{'name': 'home', 'mountPath': '/home/admin'}, {'name': 'work', 'mountPath': '/workspace'},
                                             {'name': 'config', 'mountPath': '/run/platform', 'readOnly': True}, {'name': 'run', 'mountPath': '/run/user'}]+[{'name':'resource-view','mountPath':'/'+p,'subPath':p,'readOnly':True} for p in ['proc/cpuinfo','proc/meminfo','proc/stat','proc/uptime','proc/swaps','proc/diskstats','proc/loadavg','sys/devices/system/cpu/online']]}],
            'volumes': [{'name':'resource-view','persistentVolumeClaim':{'claimName':'resource-view','readOnly':True}},{'name': 'home', 'emptyDir': {}}, {'name': 'work', 'emptyDir': {}}, {'name': 'run', 'emptyDir': {'medium': 'Memory'}},
                        {'name': 'config', 'secret': {'secretName': name, 'defaultMode': 0o440}}]}}
        try:
            for obj in (secret, pod):
                run([*kube, 'apply', '-f', '-'], input=json.dumps(obj))
            run([*kube, 'wait', '--for=condition=Ready', 'pod/' + name, '--timeout=120s'])
            resource_view = inspect_resource_view(namespace, name, 2, 2)
            if args.resource_view_only:
                print(json.dumps(resource_view))
                return
            address = json.loads(run([*kube, 'get', 'pod', name, '-o', 'json']))['status']['podIP']
            ssh = ['ssh', '-i', key, '-p', '2222', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
                   '-o', 'UserKnownHostsFile=' + str(directory / 'known_hosts'), '-o', 'ConnectTimeout=5']
            host = 'admin@' + address
            commands = '''set -e
test "$(id -u)" = 1000
test "$(whoami)" = admin
test "$HOME" = /home/admin
test "$(grep -c ^processor /proc/cpuinfo)" = 2
test "$(grep -c '^cpu[0-9]' /proc/stat)" = 2
test "$(getconf _NPROCESSORS_ONLN)" = 2
test "$(nproc)" = 2
test "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" = 2097152
grep -q ports.ubuntu.com /etc/apt/sources.list
! grep -Eq "tsinghua|aliyun" /etc/apt/sources.list
test "$(sudo -n id -u)" = 0
! command -v python3
ping -c 1 127.0.0.1 >/dev/null
timeout 10 getent hosts mirrors.tuna.tsinghua.edu.cn >/dev/null
curl -4 -fsSL --connect-timeout 5 --max-time 30 https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/pool/universe/t/tree/tree_2.0.2-1_arm64.deb -o /tmp/tree.deb
sudo -n apt-get install -y --no-install-recommends /tmp/tree.deb >/dev/null
tree --version >/dev/null
printf '#include <stdio.h>\nint main(){puts("compile-ok");}\n' >/workspace/check.c
gcc /workspace/check.c -o /workspace/check
test "$(/workspace/check)" = compile-ok
cmake --version >/dev/null
ninja --version >/dev/null
htop --version >/dev/null
bwrap --unshare-all --ro-bind / / true
grep -q force_color_prompt=yes ~/.bashrc
printf 'workspace-tools-pass\n'
'''
            result = run([*ssh, host, commands], timeout=120)
            assert 'workspace-tools-pass' in result
            color = run([*ssh, '-tt', host], input='printf "__BLE_VERSION__%s__END__\\n" "$BLE_VERSION"\nexit\n',
                        env={**os.environ, 'TERM': 'xterm-256color'}, timeout=20)
            assert re.search(r'__BLE_VERSION__[0-9][^\r\n]*__END__', color) and '\x1b[' in color, repr(color[-1200:])
            print(json.dumps({'ssh': True, 'sudo_apt_install': True, 'c_compile': True, 'network_tools': True,
                              'bubblewrap_basic_sandbox': True, 'htop': True, 'bash_highlighting_and_color': True,
                              'default_python_absent': True,'registered_username_and_home':True,'cpu_view':2,'memory_view_gib':2,'ubuntu_official_sources':True,
                              'htop_cpu_meters': resource_view['htop_cpu_meters']}))
        finally:
            for resource in ('service', 'pod', 'secret'):
                subprocess.run([*kube, 'delete', resource, name, '--ignore-not-found', '--wait=true', '--timeout=60s'], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
