#!/usr/bin/env python3
"""Install the OCI defaults used by user workspaces and nested Rootless Docker."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = Path('/etc/rancher/k3s/platform-workspace-oci.json')
TEMPLATE_PATH = Path('/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl')
HEADER = '# Managed by gpu-platform workspace-runtime.py\n'


def workspace_spec(default):
    spec = deepcopy(default)
    spec['mounts'] += [
        {'destination': '/run/platform-proc', 'type': 'proc', 'source': 'proc',
         'options': ['nosuid', 'noexec', 'nodev']},
        {'destination': '/run/platform-sys', 'type': 'sysfs', 'source': 'sysfs',
         'options': ['nosuid', 'noexec', 'nodev', 'ro']},
    ]
    # RootlessKit opens TUN inside its own network namespace.
    spec['linux'].setdefault('devices', []).append(
        {'path': '/dev/net/tun', 'type': 'c', 'major': 10, 'minor': 200,
         'fileMode': 0o666, 'uid': 0, 'gid': 0})
    spec['linux']['resources']['devices'].append(
        {'allow': True, 'type': 'c', 'major': 10, 'minor': 200, 'access': 'rwm'})
    spec.setdefault('hooks',{}).setdefault('prestart',[]).append({
        'path':'/usr/bin/python3','args':['python3',str(ROOT/'deploy/gpu_runtime.py'),'hook'],'timeout':30})
    return spec


def runtime_template(classes):
    text = HEADER + '{{ template "base" . }}\n'
    for name, binary in [(classes['cpu'], '/usr/bin/runc'),
                         (classes['gpu'], '/usr/bin/nvidia-container-runtime')]:
        table = "plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.'" + name + "'"
        text += (f'\n[{table}]\n  runtime_type = "io.containerd.runc.v2"\n'
                 f'  base_runtime_spec = "{SPEC_PATH}"\n[{table}.options]\n'
                 f'  BinaryName = "{binary}"\n  SystemdCgroup = true\n')
    return text


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def write_changed(path, content):
    if path.exists() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


def install(cfg):
    if os.geteuid() != 0:
        raise RuntimeError('Run with sudo')
    capability=run(['nvidia-cuda-mps-control','-p','3','namespace','set','--help'],capture_output=True,text=True).stdout
    if '--sm-partition' not in capability:
        raise RuntimeError('NVIDIA R620 with namespace SM partition assignments is required')
    unit=Path('/etc/systemd/system/gpu-platform-mps.service')
    if not unit.exists():
        pods=json.loads(run(['k3s','kubectl','-n',cfg['platform']['workload_namespace'],'get','pods','-o','json'],capture_output=True,text=True).stdout)['items']
        if pods:raise RuntimeError('Stop workspaces before first enabling the shared GPU runtime')
        subprocess.run(['systemctl','stop','gpu-platform-node.service'],check=False)
    for path in ['pipes','logs']:
        Path('/var/lib/gpu-platform/gpu',path).mkdir(mode=0o711,parents=True,exist_ok=True)
    write_changed(unit,'[Unit]\nDescription=NVIDIA MPS for platform GPU workloads\nAfter=network.target\n[Service]\nType=simple\nEnvironment=CUDA_MPS_PIPE_DIRECTORY=/var/lib/gpu-platform/gpu/pipes\nEnvironment=CUDA_MPS_LOG_DIRECTORY=/var/lib/gpu-platform/gpu/logs\nExecStart=/usr/bin/nvidia-cuda-mps-control -f -p 3 -m\nExecStop=/usr/bin/nvidia-cuda-mps-control -p 3 -q\nRestart=on-failure\nRestartSec=2\n[Install]\nWantedBy=multi-user.target\n')
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','gpu-platform-mps.service'])
    classes = cfg['platform']['runtime_classes']
    if TEMPLATE_PATH.exists() and not TEMPLATE_PATH.read_text().startswith(HEADER):
        raise RuntimeError('Merge the existing custom containerd template before installing workspace runtimes')
    default = json.loads(run(['k3s', 'ctr', 'oci', 'spec'], capture_output=True, text=True).stdout)
    changed = write_changed(SPEC_PATH, json.dumps(workspace_spec(default), indent=2)+'\n')
    changed = write_changed(TEMPLATE_PATH, runtime_template(classes)) or changed
    profiles = ROOT/'deploy/profiles'
    for source, target in [('workspace-seccomp.json', '/var/lib/kubelet/seccomp/gpu-platform-workspace.json'),
                           ('workspace-apparmor', '/etc/apparmor.d/gpu-platform-workspace')]:
        write_changed(Path(target), (profiles/source).read_text())
    run(['apparmor_parser', '-r', '/etc/apparmor.d/gpu-platform-workspace'])
    if changed:
        run(['systemctl', 'restart', 'k3s'])
    run(['k3s', 'kubectl', 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=120s'])
    for name in classes.values():
        obj = {'apiVersion': 'node.k8s.io/v1', 'kind': 'RuntimeClass',
               'metadata': {'name': name, 'labels': {'app.kubernetes.io/managed-by': 'gpu-platform'}},
               'handler': name}
        run(['k3s', 'kubectl', 'apply', '-f', '-'], input=json.dumps(obj), text=True)
    directory=Path('/var/lib/gpu-platform/gpu/pipes/shared')
    directory.mkdir(mode=0o711,parents=True,exist_ok=True)
    nodes=json.loads(run(['k3s','kubectl','get','nodes','-o','json'],capture_output=True,text=True).stdout)['items']
    hostname=nodes[0]['metadata']['labels']['kubernetes.io/hostname']
    pv={'apiVersion':'v1','kind':'PersistentVolume','metadata':{'name':'platform-gpu-connections'},'spec':{
        'capacity':{'storage':'1Mi'},'accessModes':['ReadOnlyMany'],'storageClassName':'','persistentVolumeReclaimPolicy':'Retain',
        'local':{'path':str(directory)},'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[
            {'key':'kubernetes.io/hostname','operator':'In','values':[hostname]}]}]}}}}
    claim={'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':'gpu-connections','namespace':cfg['platform']['workload_namespace']},
        'spec':{'accessModes':['ReadOnlyMany'],'storageClassName':'','volumeName':'platform-gpu-connections','resources':{'requests':{'storage':'1Mi'}}}}
    for obj in [pv,claim]:run(['k3s','kubectl','apply','-f','-'],input=json.dumps(obj),text=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    install(yaml.safe_load(args.config.read_text()))
